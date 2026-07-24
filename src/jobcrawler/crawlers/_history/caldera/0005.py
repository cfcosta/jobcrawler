
import json
import re
from html import unescape
from urllib.parse import urlencode

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/careers-page/beead238-de58-4e47-8bc4-b3b45b1f55d6/jobs?limit=50&offset=0"
EXTRA_HOSTS = []

_ORG_ID = "beead238-de58-4e47-8bc4-b3b45b1f55d6"
_LIST_BASE = f"https://app.dover.com/api/v1/careers-page/{_ORG_ID}/jobs"
_DETAIL_BASE = "https://app.dover.com/api/v1/inbound/application-portal-job/"
_APPLY_BASE = "https://app.dover.com/apply/caldera/"


def _description_text(value):
    text = value or ""
    text = re.sub(
        r"(?i)<\s*(br|/p|/div|/h[1-6]|/li|/ul|/ol|/tr)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?i)<\s*li\b[^>]*>", "- ", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    lines = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def _json(response):
    return json.loads(response)


def crawl(ctx):
    limit = 50
    offset = 0
    seen = set()

    while True:
        if offset == 0:
            list_url = SITE
        else:
            list_url = _LIST_BASE + "?" + urlencode({
                "limit": limit,
                "offset": offset,
            })

        response = ctx.fetch(list_url)
        page = _json(response)
        results = page.get("results") or []

        for listed in results:
            job_id = listed.get("id")
            if (
                not job_id
                or job_id in seen
                or not listed.get("is_published")
                or listed.get("is_sample")
            ):
                continue
            seen.add(job_id)

            detail_response = ctx.fetch(_DETAIL_BASE + job_id)
            detail = _json(detail_response)
            if detail.get("active") is False or detail.get("is_private") is True:
                continue

            description = _description_text(
                detail.get("user_provided_description")
            )
            if not description:
                continue

            raw_locations = detail.get("locations") or listed.get("locations") or []
            locations = [
                item["name"]
                for item in raw_locations
                if isinstance(item, dict) and item.get("name")
            ]

            primary_location = detail.get("location")
            if not primary_location and locations:
                primary_location = locations[0]
            if not primary_location:
                continue

            apply_url = _APPLY_BASE + job_id + "/"
            data = {
                "url": apply_url,
                "title": detail.get("title") or listed.get("title"),
                "company": "Caldera",
                "location": primary_location,
                "description": description,
                "source": "app.dover.com",
                "external_id": job_id,
                "apply_url": apply_url,
            }

            if locations:
                data["locations"] = locations

            workplace_type = (
                detail.get("workplace_type")
                or listed.get("workplace_type")
            )
            if workplace_type:
                data["workplace_type"] = workplace_type

            compensation = detail.get("compensation") or {}
            employment_type = compensation.get("employment_type")
            if employment_type:
                data["employment_type"] = employment_type

            created = detail.get("created")
            if created:
                data["posted_at"] = created

            salary_text = detail.get("compensation_details")
            if salary_text:
                data["salary"] = salary_text

            lower = compensation.get("lower_bound")
            upper = compensation.get("upper_bound")
            if lower is not None:
                data["salary_min"] = lower
            if upper is not None:
                data["salary_max"] = upper
            if lower is not None or upper is not None:
                currency = compensation.get("currency_code")
                period = compensation.get("salary_range_type")
                if currency:
                    data["salary_currency"] = currency
                if period:
                    data["salary_period"] = period

            yield Job(**data)

        offset += len(results)
        count = page.get("count")
        if not results or len(results) < limit:
            break
        if isinstance(count, int) and offset >= count:
            break
