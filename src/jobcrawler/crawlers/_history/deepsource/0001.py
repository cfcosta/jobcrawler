import json
import re
from html import unescape

from jobcrawler.runtime.contract import Job

SITE = "https://app.dover.com/api/v1/careers-page/02322923-27a3-439d-aadc-865046d47d15/jobs?limit=50&offset=0"
EXTRA_HOSTS = []


def _html_to_text(markup):
    markup = unescape(markup or "")
    markup = re.sub(
        r"(?is)<(script|style)\b[^>]*>.*?</\1\s*>",
        " ",
        markup,
    )
    markup = re.sub(r"(?i)<br\s*/?>", "\n", markup)
    markup = re.sub(r"(?i)<li\b[^>]*>", "\n- ", markup)
    markup = re.sub(
        r"(?i)</?(?:p|div|ul|ol|li|h1|h2|h3|h4|h5|h6)\b[^>]*>",
        "\n",
        markup,
    )
    text = re.sub(r"(?s)<[^>]*>", " ", markup)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def crawl(ctx):
    offset = 0
    limit = 50

    while True:
        list_url = (
            "https://app.dover.com/api/v1/careers-page/"
            "02322923-27a3-439d-aadc-865046d47d15/jobs"
            f"?limit={limit}&offset={offset}"
        )
        page = json.loads(ctx.fetch(list_url))
        results = page.get("results") or []

        for summary in results:
            if summary.get("is_published") is not True:
                continue
            if summary.get("is_sample") is True:
                continue

            job_id = summary.get("id")
            if not job_id:
                continue

            detail_url = (
                "https://app.dover.com/api/v1/inbound/"
                f"application-portal-job/{job_id}"
            )
            detail = json.loads(ctx.fetch(detail_url))

            if detail.get("active") is not True:
                continue
            if detail.get("is_private") is True:
                continue

            title = detail.get("title") or summary.get("title")
            description = _html_to_text(
                detail.get("user_provided_description")
            )
            location_items = (
                detail.get("locations")
                or summary.get("locations")
                or []
            )
            locations = [
                item.get("name")
                for item in location_items
                if item.get("name")
            ]
            location = next(
                (
                    item.get("name")
                    for item in location_items
                    if item.get("is_primary") and item.get("name")
                ),
                locations[0] if locations else None,
            )

            if not title or not description or not location:
                continue

            apply_url = (
                "https://app.dover.com/apply/"
                f"DeepSource/{job_id}/"
            )
            compensation = detail.get("compensation") or {}

            fields = {
                "url": apply_url,
                "title": title,
                "company": "DeepSource",
                "location": location,
                "description": description,
                "source": SITE,
                "locations": locations,
                "workplace_type": (
                    detail.get("workplace_type")
                    or summary.get("workplace_type")
                ),
                "employment_type": compensation.get("employment_type"),
                "posted_at": detail.get("created"),
                "external_id": job_id,
                "apply_url": apply_url,
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
        if not results:
            break
        if isinstance(count, int) and offset >= count:
            break
