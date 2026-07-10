
SITE = "https://api.lever.co/v0/postings/collabora?mode=json"
EXTRA_HOSTS = []

import json
import re
import html
from datetime import datetime, timezone


def _fetch_text(ctx, url):
    response = ctx.fetch(url)
    return response.text if hasattr(response, "text") else response


def _strip_html(value):
    if not value:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6])\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return text or None


def _plain_or_html(plain, html_value):
    plain = (plain or "").strip()
    if plain:
        return plain
    return _strip_html(html_value)


def _posted_at(ms):
    if ms in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).date().isoformat()
    except Exception:
        return None


def _full_description(item):
    parts = []
    seen = set()

    def add(value):
        if not value:
            return
        value = str(value).strip()
        if not value:
            return
        key = re.sub(r"\s+", " ", value).strip()
        if key and key not in seen:
            seen.add(key)
            parts.append(value)

    # Lever descriptions are intro-only; list sections and additional text complete the body.
    add(_plain_or_html(item.get("descriptionPlain"), item.get("description")))
    if item.get("descriptionBodyPlain") != item.get("descriptionPlain"):
        add(_plain_or_html(item.get("descriptionBodyPlain"), item.get("descriptionBody")))
    if item.get("openingPlain") != item.get("descriptionPlain"):
        add(_plain_or_html(item.get("openingPlain"), item.get("opening")))

    for section in item.get("lists") or []:
        title = (section.get("text") or "").strip()
        body = _plain_or_html(None, section.get("content"))
        if title and body:
            add(title + "\n" + body)
        else:
            add(title or body)

    add(_plain_or_html(item.get("additionalPlain"), item.get("additional")))
    return "\n\n".join(parts).strip()


def crawl(ctx):
    data = json.loads(_fetch_text(ctx, SITE))
    for item in data:
        categories = item.get("categories") or {}
        location = categories.get("location") or item.get("country") or "Remote"
        job = {
            "url": item.get("hostedUrl"),
            "title": item.get("text"),
            "company": "Collabora",
            "location": location,
            "description": _full_description(item),
            "source": SITE,
        }

        all_locations = categories.get("allLocations")
        if isinstance(all_locations, list) and all_locations:
            job["locations"] = [str(loc) for loc in all_locations if loc]

        optional = {
            "external_id": item.get("id"),
            "apply_url": item.get("applyUrl"),
            "posted_at": _posted_at(item.get("createdAt")),
            "workplace_type": item.get("workplaceType"),
            "department": categories.get("team"),
            "employment_type": categories.get("commitment"),
        }
        for key, value in optional.items():
            if value not in (None, ""):
                job[key] = value

        yield job
