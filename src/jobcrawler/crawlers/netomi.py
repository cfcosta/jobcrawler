
import html
import json
import re
from jobcrawler.runtime.contract import Job
from datetime import datetime, timezone

SITE = "https://api.lever.co/v0/postings/netomi?mode=json"
EXTRA_HOSTS = []


def _text(value):
    if not value:
        return None
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(?:p|div|li|ul|ol|h[1-6])\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _description(posting):
    parts = []
    intro = _text(posting.get("description"))
    if intro:
        parts.append(intro)
    for section in posting.get("lists") or []:
        body = _text(section.get("content"))
        heading = _text(section.get("text"))
        if body:
            parts.append((heading + "\n" if heading else "") + body)
    additional = _text(posting.get("additional"))
    if additional:
        parts.append(additional)
    return "\n\n".join(parts)


def crawl(ctx):
    response = ctx.fetch(SITE)
    postings = json.loads(response)
    for posting in postings:
        categories = posting.get("categories") or {}
        location = categories.get("location")
        values = {
            "url": posting.get("hostedUrl"),
            "title": posting.get("text"),
            "company": "Netomi",
            "location": location,
            "description": _description(posting),
            "source": SITE,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("department"),
        }
        all_locations = categories.get("allLocations")
        if isinstance(all_locations, list) and all_locations:
            values["locations"] = all_locations
        created_at = posting.get("createdAt")
        if created_at is not None:
            values["posted_at"] = datetime.fromtimestamp(
                created_at / 1000, tz=timezone.utc
            ).isoformat()
        yield Job(**{key: value for key, value in values.items() if value is not None})
