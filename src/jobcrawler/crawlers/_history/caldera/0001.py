import html
import json
import re

SITE = 'https://app.dover.com/api/v1/careers-page/beead238-de58-4e47-8bc4-b3b45b1f55d6/jobs?limit=50&offset=0'
EXTRA_HOSTS = []

_API_ROOT = "https://app.dover.com"
_BOARD_ID = "beead238-de58-4e47-8bc4-b3b45b1f55d6"
_COMPANY = "Caldera"
_SLUG = "caldera"
_PAGE_SIZE = 50
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _text_from_html(value):
    value = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(
        r"(?i)</?(?:p|div|h[1-6]|li|ul|ol|blockquote)[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def _page_url(offset):
    if offset == 0:
        return SITE
    return (
        _API_ROOT
        + "/api/v1/careers-page/"
        + _BOARD_ID
        + "/jobs?limit=50&offset="
        + str(offset)
    )


def crawl(ctx):
    offset = 0

    while True:
        page = json.loads(ctx.fetch(_page_url(offset)))
        results = page.get("results") or []

        for listed in results:
            if not listed.get("is_published") or listed.get("is_sample"):
                continue

            job_id = listed.get("id")
            if not isinstance(job_id, str) or not _UUID_RE.fullmatch(job_id):
                continue

            detail_url = (
                _API_ROOT + "/api/v1/inbound/application-portal-job/" + job_id
            )
            detail = json.loads(ctx.fetch(detail_url))
            if not detail.get("active") or detail.get("is_private"):
                continue

            description = _text_from_html(
                detail.get("user_provided_description") or ""
            )
            if not description:
                continue

            raw_locations = detail.get("locations") or listed.get("locations") or []
            locations = [
                entry["name"]
                for entry in raw_locations
                if isinstance(entry, dict) and entry.get("name")
            ]
            primary_location = detail.get("location")
            if not primary_location and locations:
                primary_location = locations[0]
            if not primary_location:
                primary_location = "Remote" if detail.get("workplace_type") == "REMOTE" else "Unspecified"

            apply_url = (
                _API_ROOT + "/apply/" + _SLUG + "/" + job_id + "/"
            )
            compensation = detail.get("compensation") or {}

            job = {
                "url": apply_url,
                "apply_url": apply_url,
                "title": detail.get("title") or listed.get("title"),
                "company": _COMPANY,
                "location": primary_location,
                "description": description,
                "source": SITE,
                "external_id": job_id,
            }
            if locations:
                job["locations"] = locations
            if detail.get("workplace_type"):
                job["workplace_type"] = detail["workplace_type"]
            if compensation.get("employment_type"):
                job["employment_type"] = compensation["employment_type"]
            if detail.get("created"):
                job["posted_at"] = detail["created"]

            yield job

        offset += len(results)
        count = page.get("count")
        if not results or len(results) < _PAGE_SIZE:
            break
        if isinstance(count, int) and offset >= count:
            break
