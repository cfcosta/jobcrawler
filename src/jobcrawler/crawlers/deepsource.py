
import json

from jobcrawler.runtime.contract import Job
import re
from html import unescape
from urllib.parse import urljoin

SITE = "https://app.dover.com/api/v1/careers-page/02322923-27a3-439d-aadc-865046d47d15/jobs?limit=50&offset=0"
EXTRA_HOSTS = []


def _json(response):
    if hasattr(response, "json"):
        return response.json()
    if hasattr(response, "text"):
        return json.loads(response.text)
    return json.loads(response)


def _text(value):
    value = value or ""
    value = re.sub(
        r"<(?:script|style|noscript)\b[^>]*>.*?</(?:script|style|noscript)>",
        " ",
        value,
        flags=re.I | re.S,
    )
    value = re.sub(r"<li\b[^>]*>", "\n- ", value, flags=re.I)
    value = re.sub(
        r"</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|figcaption|"
        r"figure|footer|h[1-6]|header|hr|main|nav|ol|p|pre|section|table|"
        r"tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        value,
        flags=re.I,
    )
    value = re.sub(r"<[^>]+>", " ", value)
    value = unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def crawl(ctx):
    page_url = SITE
    while page_url:
        page = _json(ctx.fetch(page_url))
        for listing in page.get("results", []):
            if not listing.get("is_published") or listing.get("is_sample"):
                continue

            job_id = listing.get("id")
            if not job_id:
                continue

            apply_url = f"https://app.dover.com/apply/DeepSource/{job_id}/"
            detail_url = (
                "https://app.dover.com/api/v1/inbound/"
                f"application-portal-job/{job_id}"
            )
            detail = _json(ctx.fetch(detail_url))
            if not detail.get("active", True) or detail.get("is_private", False):
                continue

            locations = [
                item["name"]
                for item in (
                    detail.get("locations")
                    or listing.get("locations")
                    or []
                )
                if item.get("name")
            ]
            location = detail.get("location") or (
                locations[0] if locations else None
            )
            compensation = detail.get("compensation") or {}

            fields = {
                "url": apply_url,
                "source": SITE,
                "apply_url": apply_url,
                "title": detail.get("title") or listing.get("title"),
                "company": "DeepSource",
                "location": location,
                "locations": locations or None,
                "description": _text(
                    detail.get("user_provided_description")
                ),
                "workplace_type": (
                    detail.get("workplace_type")
                    or listing.get("workplace_type")
                ),
                "employment_type": compensation.get("employment_type"),
                "posted_at": detail.get("created"),
                "external_id": job_id,
            }
            yield Job(**{
                key: value
                for key, value in fields.items()
                if value is not None
            })

        next_url = page.get("next")
        page_url = urljoin(page_url, next_url) if next_url else None
