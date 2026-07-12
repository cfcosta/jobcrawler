import json
import re
from datetime import datetime, timezone
from html import unescape

SITE = "https://api.lever.co/v0/postings/Espresso?mode=json"
EXTRA_HOSTS = []


def _plain(value):
    if not value:
        return None

    text = re.sub(
        r"(?i)<\s*(?:br|hr)\s*/?\s*>|<\s*/?\s*(?:p|div|li|ul|ol|h[1-6]|"
        r"blockquote|pre|section|table|tr|td|th)\b[^>]*>",
        "\n",
        value,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)

    lines = []
    for line in text.splitlines():
        normalized = " ".join(line.split())
        if normalized:
            lines.append(normalized)
    return "\n".join(lines) or None


def _posted_at(epoch_ms):
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp(
        int(epoch_ms) / 1000, tz=timezone.utc
    ).isoformat()


def crawl(ctx):
    response = ctx.fetch(SITE)
    raw = response.text if hasattr(response, "text") else response
    postings = json.loads(raw)

    for posting in postings:
        categories = posting.get("categories") or {}

        chunks = [_plain(posting.get("description"))]
        chunks.extend(
            _plain(section.get("content"))
            for section in posting.get("lists") or []
            if isinstance(section, dict)
        )
        chunks.append(_plain(posting.get("additional")))
        description = "\n\n".join(chunk for chunk in chunks if chunk)

        fields = {
            "url": posting.get("hostedUrl"),
            "title": posting.get("text"),
            "company": "Espresso Systems",
            "location": categories.get("location"),
            "locations": categories.get("allLocations"),
            "description": description,
            "source": SITE,
            "workplace_type": posting.get("workplaceType"),
            "employment_type": categories.get("commitment"),
            "department": categories.get("team"),
            "posted_at": _posted_at(posting.get("createdAt")),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        yield {
            key: value
            for key, value in fields.items()
            if value is not None and value != "" and value != []
        }
