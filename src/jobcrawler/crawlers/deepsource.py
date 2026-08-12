
import html
import json
import re

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/careers-page/02322923-27a3-439d-aadc-865046d47d15/jobs?limit=50&offset=0"
EXTRA_HOSTS = []


def _plain_text(value):
    if not value:
        return None
    value = re.sub(
        r"(?is)<(script|style)[^>]*>.*?</\1>",
        " ",
        value,
    )
    value = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|div|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|tr|ul)[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = value.strip()
    return value or None


def crawl(ctx):
    offset = 0

    while True:
        page_url = (
            "https://app.dover.com/api/v1/careers-page/"
            "02322923-27a3-439d-aadc-865046d47d15/jobs"
            f"?limit=50&offset={offset}"
        )
        page = json.loads(ctx.fetch(page_url))
        rows = page.get("results") or []

        for row in rows:
            if not row.get("is_published") or row.get("is_sample"):
                continue

            job_id = row.get("id")
            if not job_id:
                continue

            apply_url = f"https://app.dover.com/apply/DeepSource/{job_id}/"
            detail_url = (
                "https://app.dover.com/api/v1/inbound/"
                f"application-portal-job/{job_id}"
            )
            detail = json.loads(ctx.fetch(detail_url))

            if not detail.get("active") or detail.get("is_private"):
                continue

            title = detail.get("title") or row.get("title")
            description = _plain_text(detail.get("user_provided_description"))
            location_records = detail.get("locations") or row.get("locations") or []
            locations = [
                item.get("name")
                for item in location_records
                if isinstance(item, dict) and item.get("name")
            ]
            location = locations[0] if locations else detail.get("location")

            if not title or not description or not location:
                continue

            kwargs = {
                "url": apply_url,
                "source": SITE,
                "title": title,
                "company": "DeepSource",
                "location": location,
                "description": description,
                "external_id": str(detail.get("id") or job_id),
                "apply_url": apply_url,
            }

            if locations:
                kwargs["locations"] = locations

            workplace_type = detail.get("workplace_type") or row.get("workplace_type")
            if workplace_type:
                kwargs["workplace_type"] = workplace_type

            compensation = detail.get("compensation") or {}
            employment_type = compensation.get("employment_type")
            if employment_type:
                kwargs["employment_type"] = employment_type

            created = detail.get("created")
            if created:
                kwargs["posted_at"] = created

            yield Job(**kwargs)

        offset += len(rows)
        count = page.get("count")
        if not rows or (isinstance(count, int) and offset >= count):
            break
