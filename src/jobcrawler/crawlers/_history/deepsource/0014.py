import html
import json
import re
from urllib.parse import urljoin

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/careers-page/02322923-27a3-439d-aadc-865046d47d15/jobs?limit=50&offset=0"
EXTRA_HOSTS = []


def _plain_text(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*li(?:\s[^>]*)?>", "\n- ", text)
    text = re.sub(
        r"(?i)</?\s*(?:address|article|aside|blockquote|br|div|footer|h[1-6]|"
        r"header|hr|main|nav|ol|p|pre|section|table|tr|ul)(?:\s[^>]*)?>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def crawl(ctx):
    summaries = []
    page_url = SITE

    while page_url:
        payload = json.loads(ctx.fetch(page_url))
        summaries.extend(payload.get("results") or [])
        next_url = payload.get("next")
        page_url = urljoin(page_url, next_url) if next_url else None

    for summary in summaries:
        if not summary.get("is_published") or summary.get("is_sample"):
            continue

        job_id = summary.get("id")
        if not job_id:
            continue

        detail_url = urljoin(
            SITE,
            f"/api/v1/inbound/application-portal-job/{job_id}",
        )
        detail = json.loads(ctx.fetch(detail_url))
        if not detail.get("active") or detail.get("is_private"):
            continue

        description = _plain_text(detail.get("user_provided_description"))
        title = detail.get("title") or summary.get("title")
        if not title or not description:
            continue

        raw_locations = detail.get("locations") or summary.get("locations") or []
        locations = [
            item.get("name")
            for item in raw_locations
            if isinstance(item, dict) and item.get("name")
        ]
        primary_locations = [
            item.get("name")
            for item in raw_locations
            if isinstance(item, dict)
            and item.get("is_primary")
            and item.get("name")
        ]
        location = (
            primary_locations[0]
            if primary_locations
            else (locations[0] if locations else detail.get("location"))
        )
        if not location:
            continue

        posting_url = f"https://app.dover.com/apply/DeepSource/{job_id}/"
        compensation = detail.get("compensation") or {}
        fields = {
            "url": posting_url,
            "title": title,
            "company": "DeepSource",
            "location": location,
            "locations": locations or None,
            "description": description,
            "source": SITE,
            "workplace_type": (
                detail.get("workplace_type") or summary.get("workplace_type")
            ),
            "employment_type": compensation.get("employment_type"),
            "posted_at": detail.get("created"),
            "external_id": str(job_id),
            "apply_url": posting_url,
        }
        yield Job(**{key: value for key, value in fields.items() if value is not None})
