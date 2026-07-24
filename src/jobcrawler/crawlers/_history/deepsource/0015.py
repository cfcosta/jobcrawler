import html
import json
import re
from urllib.parse import urlencode

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/careers-page/02322923-27a3-439d-aadc-865046d47d15/jobs?limit=50&offset=0"
EXTRA_HOSTS = []

_ORG_ID = "02322923-27a3-439d-aadc-865046d47d15"
_API_ROOT = "https://app.dover.com/api/v1/careers-page/" + _ORG_ID + "/jobs"
_DETAIL_ROOT = "https://app.dover.com/api/v1/inbound/application-portal-job/"
_APPLY_ROOT = "https://app.dover.com/apply/DeepSource/"


def _json(value):
    if isinstance(value, str):
        return json.loads(value)
    return value


def _description(value):
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li(?:\s[^>]*)?>", "\n- ", text)
    text = re.sub(
        r"(?i)</?\s*(?:address|article|aside|blockquote|div|footer|h[1-6]|"
        r"header|hr|main|ol|p|pre|section|table|tr|ul)(?:\s[^>]*)?>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)

    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def crawl(ctx):
    limit = 50
    offset = 0
    total = None

    while total is None or offset < total:
        page_url = _API_ROOT + "?" + urlencode({"limit": limit, "offset": offset})
        page = _json(ctx.fetch(page_url))
        results = page.get("results") or []
        total = page.get("count", len(results))

        for listing in results:
            if listing.get("is_published") is not True:
                continue
            if listing.get("is_sample") is True:
                continue

            job_id = listing.get("id")
            if not job_id:
                continue

            detail = _json(ctx.fetch(_DETAIL_ROOT + job_id))
            if detail.get("active") is not True:
                continue
            if detail.get("is_private") is True:
                continue

            body_html = detail.get("user_provided_description")
            description = _description(body_html) if body_html else None
            title = detail.get("title") or listing.get("title")

            location_items = detail.get("locations") or listing.get("locations") or []
            locations = [
                item.get("name")
                for item in location_items
                if isinstance(item, dict) and item.get("name")
            ]
            primary = next(
                (
                    item.get("name")
                    for item in location_items
                    if isinstance(item, dict)
                    and item.get("is_primary")
                    and item.get("name")
                ),
                None,
            )
            location = detail.get("location") or primary
            if not location and locations:
                location = locations[0]

            if not title or not description or not location:
                continue

            posting_url = _APPLY_ROOT + job_id + "/"
            fields = {
                "url": posting_url,
                "apply_url": posting_url,
                "title": title,
                "company": "DeepSource",
                "location": location,
                "description": description,
                "external_id": job_id,
                "source": SITE,
            }

            if locations:
                fields["locations"] = locations
            if detail.get("workplace_type"):
                fields["workplace_type"] = detail["workplace_type"]

            compensation = detail.get("compensation") or {}
            if compensation.get("employment_type"):
                fields["employment_type"] = compensation["employment_type"]
            if detail.get("created"):
                fields["posted_at"] = detail["created"]

            yield Job(**fields)

        if not results:
            break
        offset += len(results)
