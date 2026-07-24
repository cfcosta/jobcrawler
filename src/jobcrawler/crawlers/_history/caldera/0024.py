
import json
import re
from html import unescape
from urllib.parse import urljoin

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/careers-page/beead238-de58-4e47-8bc4-b3b45b1f55d6/jobs?limit=50&offset=0"
EXTRA_HOSTS = []


def _plain_text(value):
    value = unescape(value)
    value = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|fieldset|"
        r"figcaption|figure|footer|form|h[1-6]|header|hr|li|main|nav|ol|p|pre|"
        r"section|table|tr|ul)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?s)<[^>]+>", " ", value)

    lines = []
    for line in value.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def crawl(ctx):
    page_url = SITE
    seen_pages = set()

    while page_url and page_url not in seen_pages:
        seen_pages.add(page_url)
        page = json.loads(ctx.fetch(page_url))

        for item in page.get("results", []):
            if not item.get("is_published") or item.get("is_sample"):
                continue

            job_id = item.get("id")
            title = item.get("title")
            if not job_id or not title:
                continue

            detail_url = urljoin(
                SITE,
                f"/api/v1/jobs/{job_id}/get_job_description",
            )
            detail = json.loads(ctx.fetch(detail_url))
            if not detail.get("is_published"):
                continue

            description_html = (
                detail.get("user_provided_description")
                or detail.get("user_facing_description")
            )
            if not description_html:
                continue

            description = _plain_text(description_html)
            if not description:
                continue

            location_records = item.get("locations") or []
            locations = [
                record["name"]
                for record in location_records
                if isinstance(record, dict) and record.get("name")
            ]
            if not locations:
                continue

            posting_url = urljoin(SITE, f"/apply/caldera/{job_id}/")
            fields = {
                "url": posting_url,
                "title": title,
                "company": "Caldera",
                "location": locations[0],
                "locations": locations,
                "description": description,
                "source": SITE,
                "external_id": job_id,
                "apply_url": detail.get("external_url") or posting_url,
            }

            workplace_type = item.get("workplace_type")
            if workplace_type:
                fields["workplace_type"] = workplace_type

            yield Job(**fields)

        next_url = page.get("next")
        page_url = urljoin(page_url, next_url) if next_url else None
