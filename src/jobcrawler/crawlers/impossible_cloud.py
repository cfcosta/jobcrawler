
import html
import json
import re
from datetime import datetime, timezone


SITE = "https://api.lever.co/v0/postings/impossiblecloud?mode=json"
EXTRA_HOSTS = []


def _plain_text(fragment):
    if not fragment:
        return None
    text = html.unescape(fragment)
    text = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/ul|/ol|/h[1-6]|/section|/article)\s*/?\s*>",
        "\n",
        text,
    )
    text = re.sub(r"(?i)<\s*li(?:\s[^>]*)?>", "\n- ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines) or None


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


def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))

    for posting in postings:
        categories = posting.get("categories") or {}
        fields = {
            "site": SITE,
            "source": "Lever",
            "url": posting["hostedUrl"],
            "title": posting["text"],
            "company": "Impossible Cloud",
            "location": categories["location"],
            "description": _full_description(posting),
        }

        optional = {
            "apply_url": posting.get("applyUrl"),
            "external_id": posting.get("id"),
            "department": categories.get("team"),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
        }

        locations = categories.get("allLocations")
        if locations:
            optional["locations"] = locations

        created_at = posting.get("createdAt")
        if created_at is not None:
            optional["posted_at"] = datetime.fromtimestamp(
                created_at / 1000, tz=timezone.utc
            ).isoformat()

        fields.update(
            key_value
            for key_value in optional.items()
            if key_value[1] is not None and key_value[1] != ""
        )
        yield fields
