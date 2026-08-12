
import html
import json
import re

from jobcrawler.runtime.contract import Job

SITE = "https://app.dover.com/api/v1/careers-page/02322923-27a3-439d-aadc-865046d47d15/jobs?limit=50&offset=0"
EXTRA_HOSTS = []


def _text(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(?:p|li|div|h[1-6])\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def crawl(ctx):
    url = SITE
    while url:
        page = json.loads(ctx.fetch(url))
        for item in page.get("results", []):
            if not item.get("is_published") or item.get("is_sample"):
                continue

            job_id = item.get("id")
            title = item.get("title")
            if not job_id or not title:
                continue

            apply_url = f"https://app.dover.com/apply/DeepSource/{job_id}/"
            detail_url = (
                f"https://app.dover.com/api/v1/jobs/{job_id}/"
                "get_job_description"
            )
            detail = json.loads(ctx.fetch(detail_url))
            description = _text(
                detail.get("user_provided_description")
                or detail.get("user_facing_description")
            )

            source_locations = item.get("locations") or []
            locations = [
                entry.get("name")
                for entry in source_locations
                if entry.get("name")
            ]
            primary = next(
                (
                    entry.get("name")
                    for entry in source_locations
                    if entry.get("is_primary") and entry.get("name")
                ),
                None,
            )
            location = primary or (locations[0] if locations else None)
            if not location or not description:
                continue

            yield Job(
                url=apply_url,
                title=title,
                company="DeepSource",
                location=location,
                locations=locations,
                description=description,
                source=SITE,
                workplace_type=item.get("workplace_type"),
                external_id=job_id,
                apply_url=apply_url,
            )

        url = page.get("next")
