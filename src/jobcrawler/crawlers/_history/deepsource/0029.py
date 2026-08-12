import json
import re
from html import unescape
from urllib.parse import urlencode

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/careers-page/02322923-27a3-439d-aadc-865046d47d15/jobs?limit=50&offset=0"
EXTRA_HOSTS = []

_BASE = "https://app.dover.com"
_ORG_ID = "02322923-27a3-439d-aadc-865046d47d15"
_COMPANY = "DeepSource"
_PAGE_SIZE = 50


def _text(value):
    value = value or ""
    value = re.sub(
        r"(?is)<(?:script|style|noscript|svg)\b[^>]*>.*?</(?:script|style|noscript|svg)>",
        " ",
        value,
    )
    value = re.sub(
        r"(?i)</?(?:article|blockquote|br|div|h[1-6]|li|main|ol|p|section|table|tr|ul)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n+ *", "\n", value)
    return value.strip()


def _list_url(offset):
    query = urlencode({"limit": _PAGE_SIZE, "offset": offset})
    return f"{_BASE}/api/v1/careers-page/{_ORG_ID}/jobs?{query}"


def crawl(ctx):
    offset = 0

    while True:
        page_url = SITE if offset == 0 else _list_url(offset)
        page = json.loads(ctx.fetch(page_url))
        records = page.get("results") or []

        for listed in records:
            if not listed.get("is_published") or listed.get("is_sample"):
                continue

            job_id = listed.get("id")
            if not job_id:
                continue

            apply_url = f"{_BASE}/apply/DeepSource/{job_id}/"
            detail_url = f"{_BASE}/api/v1/inbound/application-portal-job/{job_id}"
            detail = json.loads(ctx.fetch(detail_url))

            if not detail.get("active") or detail.get("is_private"):
                continue

            description = _text(detail.get("user_provided_description"))
            title = detail.get("title") or listed.get("title")
            if not title or not description:
                continue

            raw_locations = detail.get("locations") or listed.get("locations") or []
            locations = [
                item.get("name")
                for item in raw_locations
                if isinstance(item, dict) and item.get("name")
            ]
            location = locations[0] if locations else detail.get("location")
            if not location:
                continue

            values = {
                "url": apply_url,
                "title": title,
                "company": _COMPANY,
                "location": location,
                "description": description,
                "source": SITE,
                "external_id": job_id,
                "apply_url": apply_url,
            }

            if locations:
                values["locations"] = locations
            if detail.get("workplace_type"):
                values["workplace_type"] = detail["workplace_type"]
            if detail.get("created"):
                values["posted_at"] = detail["created"]

            compensation = detail.get("compensation") or {}
            if compensation.get("employment_type"):
                values["employment_type"] = compensation["employment_type"]

            if (
                compensation.get("open_to_sharing_comp")
                and compensation.get("lower_bound") is not None
                and compensation.get("upper_bound") is not None
            ):
                values["salary_min"] = compensation["lower_bound"]
                values["salary_max"] = compensation["upper_bound"]
                if compensation.get("currency_code"):
                    values["salary_currency"] = compensation["currency_code"]
                if compensation.get("salary_range_type"):
                    values["salary_period"] = compensation["salary_range_type"]
                if detail.get("compensation_details"):
                    values["salary"] = detail["compensation_details"]

            yield Job(**values)

        offset += len(records)
        count = page.get("count")
        if not records or page.get("next") is None:
            break
        if isinstance(count, int) and offset >= count:
            break
