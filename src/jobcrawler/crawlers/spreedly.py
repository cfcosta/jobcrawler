from datetime import datetime, timezone
from html import unescape
import json
import re

SITE = "https://api.lever.co/v0/postings/spreedly?mode=json"
EXTRA_HOSTS = []


def _plain_text(value):
    if not value:
        return None
    text = unescape(value)
    text = re.sub(
        r"(?i)<\s*(?:br|p|div|li|ul|ol|h[1-6]|blockquote|tr|table|section|article|pre|hr)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(
        r"(?i)</\s*(?:p|div|li|ul|ol|h[1-6]|blockquote|tr|table|section|article|pre)\s*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    return text or None


def _full_description(posting):
    sections = []

    intro = _plain_text(posting.get("description"))
    if intro:
        sections.append(intro)

    for item in posting.get("lists") or []:
        heading = _plain_text(item.get("text"))
        content = _plain_text(item.get("content"))
        section = "\n".join(part for part in (heading, content) if part)
        if section:
            sections.append(section)

    additional = _plain_text(posting.get("additional"))
    if additional:
        sections.append(additional)

    return "\n\n".join(sections)


def _posted_at(epoch_ms):
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).isoformat()


def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))

    for posting in postings:
        categories = posting.get("categories") or {}
        kwargs = {
            "url": posting.get("hostedUrl"),
            "source": SITE,
            "title": posting.get("text"),
            "company": "Spreedly",
            "location": categories.get("location"),
            "description": _full_description(posting),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("team"),
            "posted_at": _posted_at(posting.get("createdAt")),
        }

        all_locations = categories.get("allLocations")
        if isinstance(all_locations, list) and all_locations:
            kwargs["locations"] = all_locations

        yield {key: value for key, value in kwargs.items() if value is not None}
