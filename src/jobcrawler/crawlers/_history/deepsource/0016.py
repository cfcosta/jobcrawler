
import json
import re
from html import unescape

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/careers-page/02322923-27a3-439d-aadc-865046d47d15/jobs?limit=50&offset=0"
EXTRA_HOSTS = []

COMPANY = "DeepSource"
HOST = "https://app.dover.com"


def _absolute_url(value):
    if not value:
        return None
    if value.startswith("https://app.dover.com/"):
        return value
    if value.startswith("/"):
        return HOST + value
    return HOST + "/" + value


def _html_to_text(value):
    text = re.sub(
        r"(?i)<\s*(?:br|li|p|div|h[1-6]|tr|section|article)[^>]*>",
        "\n",
        value or "",
    )
    text = re.sub(
        r"(?i)<\s*/\s*(?:li|p|div|h[1-6]|tr|section|article|ul|ol)\s*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)

    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def crawl(ctx):
    next_url = SITE
    seen_pages = set()
    seen_jobs = set()

    while next_url and next_url not in seen_pages:
        seen_pages.add(next_url)
        payload = json.loads(ctx.fetch(next_url))

        for summary in payload.get("results", []):
            job_id = summary.get("id")
            if (
                not job_id
                or job_id in seen_jobs
                or not summary.get("is_published")
                or summary.get("is_sample")
            ):
                continue

            seen_jobs.add(job_id)
            detail_url = (
                HOST
                + "/api/v1/inbound/application-portal-job/"
                + job_id
            )
            detail = json.loads(ctx.fetch(detail_url))
            if not detail.get("active") or detail.get("is_private"):
                continue

            description = _html_to_text(
                detail.get("user_provided_description")
            )
            if not description:
                continue

            location_items = (
                detail.get("locations")
                or summary.get("locations")
                or []
            )
            locations = [
                item.get("name")
                for item in location_items
                if isinstance(item, dict) and item.get("name")
            ]
            location = locations[0] if locations else detail.get("location")
            title = detail.get("title") or summary.get("title")
            if not title or not location:
                continue

            apply_url = (
                HOST + "/apply/" + COMPANY + "/" + job_id + "/"
            )
            compensation = detail.get("compensation") or {}

            fields = {
                "url": apply_url,
                "title": title,
                "company": COMPANY,
                "source": "Dover",
                "location": location,
                "locations": locations or None,
                "description": description,
                "workplace_type": (
                    detail.get("workplace_type")
                    or summary.get("workplace_type")
                ),
                "employment_type": compensation.get("employment_type"),
                "posted_at": detail.get("created"),
                "external_id": job_id,
                "apply_url": apply_url,
            }

            yield Job(**{
                key: value
                for key, value in fields.items()
                if value is not None and value != ""
            })

        next_url = _absolute_url(payload.get("next"))
