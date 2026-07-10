
import html
import json
import re
from datetime import datetime, timezone

SITE = "https://api.lever.co/v0/postings/tractian?mode=json"
EXTRA_HOSTS = []

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")


def _clean_html(value):
    if not value:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|tr)\s*>", "\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    lines = [_WS_RE.sub(" ", line).strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    return text or None


def _posted_at(ms):
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()
    except Exception:
        return None


def _full_description(posting):
    parts = []
    intro = _clean_html(posting.get("descriptionPlain") or posting.get("description"))
    if intro:
        parts.append(intro)

    for section in posting.get("lists") or []:
        heading = _clean_html(section.get("text"))
        content = _clean_html(section.get("content"))
        if heading and content:
            parts.append(heading + "\n" + content)
        elif content:
            parts.append(content)
        elif heading:
            parts.append(heading)

    additional = _clean_html(posting.get("additionalPlain") or posting.get("additional"))
    if additional:
        parts.append(additional)

    return "\n\n".join(parts)


def crawl(ctx):
    raw = ctx.fetch(SITE)
    postings = json.loads(raw)

    for posting in postings:
        categories = posting.get("categories") or {}
        title = posting.get("text")
        url = posting.get("hostedUrl")
        location = categories.get("location")
        description = _full_description(posting)

        if not (title and url and location and description):
            continue

        job = {
            "url": url,
            "title": title,
            "company": "TRACTIAN",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "department": categories.get("team") or categories.get("department"),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "posted_at": _posted_at(posting.get("createdAt")),
        }

        all_locations = categories.get("allLocations")
        if isinstance(all_locations, list):
            locations = [loc for loc in all_locations if loc]
            if locations:
                job["locations"] = locations

        yield {key: value for key, value in job.items() if value is not None and value != ""}
