
import json
import re
import html
from datetime import datetime, timezone

SITE = "https://api.lever.co/v0/postings/toptal?mode=json"
EXTRA_HOSTS = []

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")

def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr|ul|ol)\s*>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None

def _epoch_ms_to_iso(value):
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).isoformat()
    except Exception:
        return None

def _full_description(posting):
    parts = []
    desc_plain = posting.get("descriptionPlain")
    if desc_plain:
        parts.append(desc_plain.strip())
    elif posting.get("description"):
        cleaned = _clean_html(posting.get("description"))
        if cleaned:
            parts.append(cleaned)

    for block in posting.get("lists") or []:
        heading = block.get("text")
        content = block.get("content")
        block_parts = []
        if heading:
            block_parts.append(str(heading).strip())
        cleaned = _clean_html(content)
        if cleaned:
            block_parts.append(cleaned)
        if block_parts:
            parts.append("\n".join(block_parts))

    add_plain = posting.get("additionalPlain")
    if add_plain:
        parts.append(add_plain.strip())
    elif posting.get("additional"):
        cleaned = _clean_html(posting.get("additional"))
        if cleaned:
            parts.append(cleaned)

    return "\n\n".join(part for part in parts if part).strip()

def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))
    for posting in postings:
        categories = posting.get("categories") or {}
        title = posting.get("text")
        url = posting.get("hostedUrl")
        description = _full_description(posting)
        location = categories.get("location")
        if not (title and url and description and location):
            continue

        job = {
            "url": url,
            "title": title,
            "company": "Toptal",
            "location": location,
            "description": description,
            "source": SITE,
        }

        all_locations = categories.get("allLocations")
        if isinstance(all_locations, list) and all_locations:
            job["locations"] = [str(loc) for loc in all_locations if loc]

        if posting.get("id"):
            job["external_id"] = posting.get("id")
        if posting.get("applyUrl"):
            job["apply_url"] = posting.get("applyUrl")
        if categories.get("commitment"):
            job["employment_type"] = categories.get("commitment")
        if posting.get("workplaceType"):
            job["workplace_type"] = posting.get("workplaceType")
        if categories.get("department"):
            job["department"] = categories.get("department")
        elif categories.get("team"):
            job["department"] = categories.get("team")

        posted_at = _epoch_ms_to_iso(posting.get("createdAt"))
        if posted_at:
            job["posted_at"] = posted_at

        yield job
