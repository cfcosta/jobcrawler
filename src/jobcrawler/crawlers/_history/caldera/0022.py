
import html
import json
import re
from urllib.parse import urlencode

from jobcrawler.runtime.contract import Job

SITE = "https://app.dover.com/api/v1/careers-page/beead238-de58-4e47-8bc4-b3b45b1f55d6/jobs?limit=50&offset=0"
EXTRA_HOSTS = []

_BOARD_API = "https://app.dover.com/api/v1/careers-page/beead238-de58-4e47-8bc4-b3b45b1f55d6/jobs"
_DETAIL_API = "https://app.dover.com/api/v1/inbound/application-portal-job/"
_APPLY_BASE = "https://app.dover.com/apply/caldera/"
_PAGE_SIZE = 50


def _plain_text(value):
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li(?:\s[^>]*)?>", "\n- ", text)
    text = re.sub(
        r"(?i)</?\s*(?:address|article|aside|blockquote|div|dl|dt|dd|"
        r"figcaption|figure|footer|h[1-6]|header|hr|main|nav|ol|p|pre|"
        r"section|table|tr|ul)(?:\s[^>]*)?>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _location_names(record):
    names = []
    for location in record.get("locations") or []:
        name = location.get("name")
        if name:
            names.append(name)
    return names


def crawl(ctx):
    offset = 0
    total = None

    while total is None or offset < total:
        query = urlencode({"limit": _PAGE_SIZE, "offset": offset})
        page = json.loads(ctx.fetch(f"{_BOARD_API}?{query}"))
        total = page.get("count", 0)
        results = page.get("results") or []

        for listed in results:
            if not listed.get("is_published") or listed.get("is_sample"):
                continue

            job_id = listed.get("id")
            if not job_id:
                continue

            detail = json.loads(ctx.fetch(_DETAIL_API + job_id))
            if not detail.get("active") or detail.get("is_private"):
                continue

            description_html = detail.get("user_provided_description")
            if not description_html:
                continue
            description = _plain_text(description_html)
            if not description:
                continue

            locations = _location_names(detail) or _location_names(listed)
            display_location = detail.get("location")
            if not display_location and locations:
                display_location = locations[0]
            if not display_location:
                continue

            canonical_url = f"{_APPLY_BASE}{job_id}/"
            fields = {
                "url": canonical_url,
                "source": SITE,
                "apply_url": canonical_url,
                "title": detail.get("title") or listed.get("title"),
                "company": "Caldera",
                "location": display_location,
                "description": description,
                "external_id": job_id,
            }

            if locations:
                fields["locations"] = locations

            workplace_type = detail.get("workplace_type") or listed.get("workplace_type")
            if workplace_type:
                fields["workplace_type"] = workplace_type

            compensation = detail.get("compensation") or {}
            employment_type = compensation.get("employment_type")
            if employment_type:
                fields["employment_type"] = employment_type

            created = detail.get("created")
            if created:
                fields["posted_at"] = created

            yield Job(**fields)

        if not results:
            break
        offset += len(results)
