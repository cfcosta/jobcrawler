import json
import re
from datetime import datetime, timezone
from jobcrawler.runtime.contract import Job


SITE = "https://api.lever.co/v0/postings/observablehq?mode=json"
EXTRA_HOSTS = []


_NAMED_ENTITIES = {
    "amp": "&",
    "apos": "'",
    "gt": ">",
    "lt": "<",
    "nbsp": " ",
    "quot": '"',
}


def _entity(match):
    value = match.group(1)
    if value.startswith("#x") or value.startswith("#X"):
        try:
            return chr(int(value[2:], 16))
        except ValueError:
            return match.group(0)
    if value.startswith("#"):
        try:
            return chr(int(value[1:]))
        except ValueError:
            return match.group(0)
    return _NAMED_ENTITIES.get(value, match.group(0))


def _text(value):
    if not value:
        return None
    text = str(value)
    text = re.sub(
        r"<\s*(?:br|/p|/div|/li|/ul|/ol|/h[1-6]|/tr|/section)\s*/?\s*>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&(#(?:[xX][0-9a-fA-F]+|\d+)|[A-Za-z]+);", _entity, text)
    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines) or None


def _posted_at(value):
    if not isinstance(value, (int, float)):
        return None
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()


def crawl(ctx):
    response = ctx.fetch(SITE)
    if isinstance(response, str):
        postings = json.loads(response)
    else:
        postings = response.json()

    if not isinstance(postings, list):
        return

    for posting in postings:
        if not isinstance(posting, dict):
            continue

        categories = posting.get("categories") or {}
        location = categories.get("location")
        locations = categories.get("allLocations")
        if not isinstance(locations, list) or not locations:
            locations = [location] if location else None

        sections = [_text(posting.get("description"))]
        for section in posting.get("lists") or []:
            if isinstance(section, dict):
                sections.append(_text(section.get("content")))
        sections.append(_text(posting.get("additional")))
        description = "\n\n".join(section for section in sections if section)

        url = posting.get("hostedUrl")
        title = posting.get("text")
        if not url or not title or not location or not description:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Observable",
            "location": location,
            "description": description,
            "source": SITE,
        }
        optional = {
            "locations": locations,
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("team"),
            "posted_at": _posted_at(posting.get("createdAt")),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        fields.update({key: value for key, value in optional.items() if value is not None})
        yield Job(**fields)
