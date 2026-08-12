import html
import json
import re
from urllib.parse import urlencode

from jobcrawler.runtime.contract import Job

SITE = 'https://app.dover.com/api/v1/careers-page/beead238-de58-4e47-8bc4-b3b45b1f55d6/jobs?limit=50&offset=0'
EXTRA_HOSTS = []

_API_ROOT = "https://app.dover.com"
_BOARD_ID = "beead238-de58-4e47-8bc4-b3b45b1f55d6"
_COMPANY = "Caldera"
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_BLOCK_RE = re.compile(
    r"</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|fieldset|"
    r"figcaption|figure|footer|form|h[1-6]|header|hr|li|main|nav|ol|p|"
    r"pre|section|table|tr|ul)\b[^>]*>",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _response_text(response):
    if isinstance(response, str):
        return response
    if isinstance(response, bytes):
        return response.decode("utf-8")
    value = response.read()
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


def _plain_text(value):
    value = _BLOCK_RE.sub("\n", value or "")
    value = _TAG_RE.sub("", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def crawl(ctx):
    limit = 50
    offset = 0

    while True:
        query = urlencode({"limit": limit, "offset": offset})
        list_url = f"{_API_ROOT}/api/v1/careers-page/{_BOARD_ID}/jobs?{query}"
        response = ctx.fetch(list_url)
        payload = json.loads(_response_text(response))
        rows = payload.get("results") or []

        for row in rows:
            if row.get("is_published") is not True or row.get("is_sample") is True:
                continue

            job_id = row.get("id")
            if not isinstance(job_id, str) or not _UUID_RE.fullmatch(job_id):
                continue

            detail_url = f"{_API_ROOT}/api/v1/inbound/application-portal-job/{job_id}"
            detail_response = ctx.fetch(detail_url)
            detail = json.loads(_response_text(detail_response))

            if detail.get("active") is not True or detail.get("is_private") is True:
                continue

            description = _plain_text(detail.get("user_provided_description"))
            title = detail.get("title") or row.get("title")
            if not title or not description:
                continue

            location_rows = detail.get("locations") or row.get("locations") or []
            locations = [
                item["name"]
                for item in location_rows
                if isinstance(item, dict) and item.get("name")
            ]
            primary = next(
                (
                    item.get("name")
                    for item in location_rows
                    if isinstance(item, dict)
                    and item.get("is_primary")
                    and item.get("name")
                ),
                None,
            )
            location = detail.get("location") or primary or (
                locations[0] if locations else None
            )
            if not location:
                continue

            apply_url = f"{_API_ROOT}/apply/caldera/{job_id}/"
            compensation = detail.get("compensation") or {}
            kwargs = {
                "url": apply_url,
                "title": title,
                "company": _COMPANY,
                "location": location,
                "description": description,
                "source": "Dover",
                "external_id": job_id,
                "apply_url": apply_url,
            }

            if locations:
                kwargs["locations"] = locations
            workplace_type = detail.get("workplace_type") or row.get("workplace_type")
            if workplace_type:
                kwargs["workplace_type"] = workplace_type
            employment_type = compensation.get("employment_type")
            if employment_type:
                kwargs["employment_type"] = employment_type
            created = detail.get("created")
            if created:
                kwargs["posted_at"] = created

            yield Job(**kwargs)

        offset += len(rows)
        count = payload.get("count")
        if not rows or (isinstance(count, int) and offset >= count):
            break
