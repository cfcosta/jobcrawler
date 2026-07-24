import html
import json
import re

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/careers-page/02322923-27a3-439d-aadc-865046d47d15/jobs?limit=50&offset=0"
EXTRA_HOSTS = []

_COMPANY = "DeepSource"
_PAGE_SIZE = 50
_LIST_BASE = "https://app.dover.com/api/v1/careers-page/02322923-27a3-439d-aadc-865046d47d15/jobs"


def _plain_text(value):
    value = value or ""
    value = re.sub(
        r"<\s*br\s*/?\s*>|<\s*/\s*(?:p|div|li|ul|ol|h[1-6]|section|article|tr)\s*>",
        "\n",
        value,
        flags=re.I,
    )
    value = re.sub(r"<\s*li(?:\s[^>]*)?>", "\n- ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    lines = []
    for line in value.splitlines():
        line = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _json(response):
    if hasattr(response, "json"):
        return response.json()
    return json.loads(response)


def crawl(ctx):
    offset = 0
    seen = set()

    while True:
        page_url = f"{_LIST_BASE}?limit={_PAGE_SIZE}&offset={offset}"
        page = _json(ctx.fetch(page_url))
        results = page.get("results") or []

        for summary in results:
            job_id = summary.get("id")
            if not job_id or job_id in seen:
                continue
            seen.add(job_id)

            if summary.get("is_published") is not True or summary.get("is_sample") is True:
                continue

            detail_url = (
                "https://app.dover.com/api/v1/inbound/application-portal-job/"
                + job_id
            )
            detail = _json(ctx.fetch(detail_url))
            if detail.get("active") is not True or detail.get("is_private") is True:
                continue

            description = _plain_text(detail.get("user_provided_description"))
            if not description:
                continue

            location_objects = detail.get("locations") or summary.get("locations") or []
            locations = [
                item["name"]
                for item in location_objects
                if isinstance(item, dict) and item.get("name")
            ]
            primary = next(
                (
                    item.get("name")
                    for item in location_objects
                    if isinstance(item, dict)
                    and item.get("is_primary")
                    and item.get("name")
                ),
                None,
            )
            location = primary or (locations[0] if locations else detail.get("location"))
            if not location:
                continue

            apply_url = f"https://app.dover.com/apply/DeepSource/{job_id}/"
            compensation = detail.get("compensation") or {}
            fields = {
                "url": apply_url,
                "source": SITE,
                "apply_url": apply_url,
                "title": detail.get("title") or summary.get("title"),
                "company": _COMPANY,
                "location": location,
                "locations": locations or None,
                "description": description,
                "external_id": job_id,
                "workplace_type": detail.get("workplace_type")
                or summary.get("workplace_type"),
                "employment_type": compensation.get("employment_type"),
                "posted_at": detail.get("created"),
            }
            yield Job(
                **{
                    key: value
                    for key, value in fields.items()
                    if value is not None
                }
            )

        offset += len(results)
        count = page.get("count")
        if not results or (isinstance(count, int) and offset >= count):
            break
