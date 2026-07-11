
import json
import re
from datetime import datetime, timezone
from html import unescape

SITE = "https://api.lever.co/v0/postings/meili?mode=json"
EXTRA_HOSTS = []


def _text_from_html(value):
    if not value:
        return None
    value = re.sub(r"(?is)<(br|/p|/li|/h[1-6])\b[^>]*>", "\n", value)
    value = re.sub(r"(?is)<[^>]+>", " ", value)
    value = unescape(value)
    return _clean(value)


def _clean(value):
    if value is None:
        return None
    value = str(value).replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = value.strip()
    return value or None


def _first_plain(post, *keys):
    for key in keys:
        value = _clean(post.get(key))
        if value:
            return value
    return None


def _list_section_text(section):
    parts = []
    heading = _clean(section.get("text"))
    if heading:
        parts.append(heading)

    # Lever supplies either plaintext content or HTML content depending on board/API version.
    content = _clean(section.get("contentPlain") or section.get("contentText"))
    if not content:
        content = _text_from_html(section.get("content"))
    if content:
        parts.append(content)

    return _clean("\n".join(parts))


def _description(post):
    parts = []

    intro = _first_plain(post, "descriptionPlain", "description")
    if not intro:
        intro = _text_from_html(post.get("description"))
    if intro:
        parts.append(intro)

    for section in post.get("lists") or []:
        text = _list_section_text(section)
        if text:
            parts.append(text)

    additional = _first_plain(post, "additionalPlain", "additional")
    if not additional:
        additional = _text_from_html(post.get("additional"))
    if additional:
        parts.append(additional)

    return _clean("\n\n".join(parts))


def _posted_at(ms):
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).date().isoformat()
    except Exception:
        return None


def crawl(ctx):
    response = ctx.fetch(SITE)
    raw = response.text if hasattr(response, "text") else str(response)
    postings = json.loads(raw)

    for post in postings:
        categories = post.get("categories") or {}
        title = _clean(post.get("text"))
        description = _description(post)
        url = _clean(post.get("hostedUrl"))
        location = _clean(categories.get("location"))

        job = {
            "url": url,
            "title": title,
            "company": "MeiliSearch",
            "location": location,
            "description": description,
            "source": SITE,
        }

        external_id = _clean(post.get("id"))
        if external_id:
            job["external_id"] = external_id

        apply_url = _clean(post.get("applyUrl"))
        if apply_url:
            job["apply_url"] = apply_url

        department = _clean(categories.get("team"))
        if department:
            job["department"] = department

        employment_type = _clean(categories.get("commitment"))
        if employment_type:
            job["employment_type"] = employment_type

        workplace_type = _clean(post.get("workplaceType"))
        if workplace_type:
            job["workplace_type"] = workplace_type

        locations = [_clean(loc) for loc in (categories.get("allLocations") or [])]
        locations = [loc for loc in locations if loc]
        if locations:
            job["locations"] = locations

        posted_at = _posted_at(post.get("createdAt"))
        if posted_at:
            job["posted_at"] = posted_at

        # Only yield complete postings; empty strings are never emitted.
        if job["url"] and job["title"] and job["location"] and job["description"]:
            yield job
