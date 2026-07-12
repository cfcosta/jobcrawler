
import datetime
import html
import json
import re

SITE = "https://api.lever.co/v0/postings/mistral?mode=json"


def _body(response):
    if hasattr(response, "text"):
        return response.text
    return response


def _strip_html(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</p\s*>", "\n\n", value)
    value = re.sub(r"(?i)</li\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _first_text(*values):
    for value in values:
        if value:
            text = value.strip()
            if text:
                return text
    return None


def _plain_or_html(obj, plain_key, html_key):
    text = _first_text(obj.get(plain_key))
    if text:
        return text
    return _strip_html(obj.get(html_key))


def _posted_at(created_at):
    if created_at is None:
        return None
    try:
        return datetime.datetime.fromtimestamp(int(created_at) / 1000, datetime.timezone.utc).date().isoformat()
    except Exception:
        return None


def _description(posting):
    parts = []

    opening = _plain_or_html(posting, "openingPlain", "opening")
    if opening:
        parts.append(opening)

    intro = _plain_or_html(posting, "descriptionPlain", "description")
    if intro and intro not in parts:
        parts.append(intro)

    body = _plain_or_html(posting, "descriptionBodyPlain", "descriptionBody")
    if body and body not in parts:
        parts.append(body)

    for section in posting.get("lists") or []:
        heading = _first_text(section.get("text"))
        content = _plain_or_html(section, "contentPlain", "content")
        if heading and content:
            parts.append(heading + "\n" + content)
        elif heading:
            parts.append(heading)
        elif content:
            parts.append(content)

    additional = _plain_or_html(posting, "additionalPlain", "additional")
    if additional:
        parts.append(additional)

    description = "\n\n".join(part for part in parts if part)
    description = re.sub(r"\n{3,}", "\n\n", description).strip()
    return description or None


def crawl(ctx):
    data = json.loads(_body(ctx.fetch(SITE)))
    for posting in data:
        title = _first_text(posting.get("text"))
        description = _description(posting)
        url = _first_text(posting.get("hostedUrl"))
        categories = posting.get("categories") or {}

        location = _first_text(categories.get("location"))
        if not location:
            all_locations = categories.get("allLocations") or []
            location = ", ".join(loc for loc in all_locations if loc) or None
        if not location:
            location = _first_text(posting.get("country"))

        if not (title and description and url and location):
            continue

        job = {
            "url": url,
            "title": title,
            "company": "Mistral AI",
            "location": location,
            "description": description,
            "source": SITE,
        }

        field_map = [
            ("id", "external_id"),
            ("applyUrl", "apply_url"),
            ("workplaceType", "workplace_type"),
        ]
        for source_key, target_key in field_map:
            value = _first_text(posting.get(source_key))
            if value:
                job[target_key] = value

        employment_type = _first_text(categories.get("commitment"))
        if employment_type:
            job["employment_type"] = employment_type

        department = _first_text(categories.get("team"))
        if department:
            job["department"] = department

        posted_at = _posted_at(posting.get("createdAt"))
        if posted_at:
            job["posted_at"] = posted_at

        yield job
