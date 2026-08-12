
import html
import json
import re

from jobcrawler.runtime.contract import Job
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SITE = "https://app.dover.com/api/v1/careers-page/beead238-de58-4e47-8bc4-b3b45b1f55d6/jobs?limit=50&offset=0"
EXTRA_HOSTS = []

_BOARD_ID = "beead238-de58-4e47-8bc4-b3b45b1f55d6"
_COMPANY = "Caldera"
_SLUG = "caldera"


def _json(response):
    return json.loads(response)


def _page_url(offset):
    parts = urlsplit(SITE)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["limit"] = "50"
    query["offset"] = str(offset)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _text_from_html(value):
    if not value:
        return None
    value = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1>", " ", value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(?:p|div|h[1-6]|li|ul|ol)>", "\n", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value).replace("\xa0", " ")
    lines = []
    for line in value.splitlines():
        line = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines) or None


def crawl(ctx):
    offset = 0
    seen = set()

    while True:
        page = _json(ctx.fetch(_page_url(offset)))
        results = page.get("results") or []

        for summary in results:
            job_id = summary.get("id")
            if (
                not job_id
                or job_id in seen
                or not summary.get("is_published")
                or summary.get("is_sample")
            ):
                continue
            seen.add(job_id)

            detail_url = (
                "https://app.dover.com/api/v1/inbound/application-portal-job/"
                + job_id
            )
            detail = _json(ctx.fetch(detail_url))
            if not detail.get("active") or detail.get("is_private"):
                continue

            description = _text_from_html(detail.get("user_provided_description"))
            if not description:
                continue

            location_items = detail.get("locations") or summary.get("locations") or []
            locations = [
                item.get("name") or (item.get("location_option") or {}).get("display_name")
                for item in location_items
            ]
            locations = [value for value in locations if value]
            primary = next(
                (
                    item.get("name")
                    or (item.get("location_option") or {}).get("display_name")
                    for item in location_items
                    if item.get("is_primary")
                ),
                None,
            )
            location = primary or (locations[0] if locations else None)
            if not location:
                location = detail.get("location")
            if not location:
                continue

            apply_url = f"https://app.dover.com/apply/{_SLUG}/{job_id}/"
            compensation = detail.get("compensation") or {}

            fields = {
                "url": apply_url,
                "title": detail.get("title") or summary.get("title"),
                "company": _COMPANY,
                "location": location,
                "locations": locations or None,
                "description": description,
                "source": SITE,
                "external_id": job_id,
                "apply_url": apply_url,
                "workplace_type": detail.get("workplace_type") or summary.get("workplace_type"),
                "employment_type": compensation.get("employment_type"),
                "posted_at": detail.get("created"),
            }
            yield Job(**{key: value for key, value in fields.items() if value is not None})

        if not page.get("next") or not results:
            break
        offset += len(results)
