
import json
import re
from html import unescape
from jobcrawler.runtime.contract import Job
from urllib.parse import urlencode, urlsplit, urlunsplit

SITE = "https://app.dover.com/api/v1/careers-page/beead238-de58-4e47-8bc4-b3b45b1f55d6/jobs?limit=50&offset=0"
EXTRA_HOSTS = []

_COMPANY = "Caldera"
_CLIENT_SLUG = "caldera"
_PAGE_SIZE = 50


def _plain_text(value):
    if not value:
        return None

    # Preserve section and list boundaries before removing markup.
    value = re.sub(
        r"(?i)<\s*(?:br|hr)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(
        r"(?i)</?\s*(?:address|article|aside|blockquote|dd|div|dl|dt|"
        r"figcaption|figure|footer|h[1-6]|header|li|main|nav|ol|p|pre|"
        r"section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?s)<[^>]+>", "", value)
    value = unescape(value).replace("\xa0", " ")

    lines = [
        re.sub(r"[ \t]+", " ", line).strip()
        for line in value.splitlines()
    ]
    text = "\n".join(line for line in lines if line)
    return text or None


def _page_url(offset):
    parts = urlsplit(SITE)
    query = urlencode({"limit": _PAGE_SIZE, "offset": offset})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def crawl(ctx):
    offset = 0

    while True:
        page = json.loads(ctx.fetch(SITE if offset == 0 else _page_url(offset)))
        records = page.get("results") or []

        for summary in records:
            if not summary.get("is_published") or summary.get("is_sample"):
                continue

            job_id = summary.get("id")
            if not job_id:
                continue

            detail_url = (
                "https://app.dover.com/api/v1/inbound/application-portal-job/"
                + str(job_id)
            )
            detail = json.loads(ctx.fetch(detail_url))

            if detail.get("active") is False or detail.get("is_private") is True:
                continue

            description = _plain_text(detail.get("user_provided_description"))
            title = detail.get("title") or summary.get("title")
            if not title or not description:
                continue

            location_records = detail.get("locations") or summary.get("locations") or []
            locations = [
                item.get("name")
                for item in location_records
                if isinstance(item, dict) and item.get("name")
            ]
            primary = next(
                (
                    item.get("name")
                    for item in location_records
                    if isinstance(item, dict)
                    and item.get("is_primary")
                    and item.get("name")
                ),
                None,
            )
            location = primary or (locations[0] if locations else None)
            if not location:
                location = detail.get("location")
            if not location:
                continue

            posting_url = (
                "https://app.dover.com/apply/"
                + _CLIENT_SLUG
                + "/"
                + str(job_id)
                + "/"
            )

            data = {
                "url": posting_url,
                "apply_url": posting_url,
                "title": title,
                "company": _COMPANY,
                "location": location,
                "description": description,
                "source": "Dover",
                "external_id": str(job_id),
            }

            if locations:
                data["locations"] = locations

            workplace_type = detail.get("workplace_type") or summary.get("workplace_type")
            if workplace_type:
                data["workplace_type"] = workplace_type

            compensation = detail.get("compensation")
            if isinstance(compensation, dict):
                employment_type = compensation.get("employment_type")
                if employment_type:
                    data["employment_type"] = employment_type

                lower = compensation.get("lower_bound")
                upper = compensation.get("upper_bound")
                if lower is not None or upper is not None:
                    if lower is not None:
                        data["salary_min"] = lower
                    if upper is not None:
                        data["salary_max"] = upper
                    if compensation.get("currency_code"):
                        data["salary_currency"] = compensation["currency_code"]
                    if compensation.get("salary_range_type"):
                        data["salary_period"] = compensation["salary_range_type"]

            if detail.get("compensation_details"):
                data["salary"] = detail["compensation_details"]

            if detail.get("created"):
                data["posted_at"] = detail["created"]

            yield Job(**data)

        offset += len(records)
        total = page.get("count")
        if not records or (isinstance(total, int) and offset >= total):
            break
