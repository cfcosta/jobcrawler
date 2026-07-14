
import json
import re
import html
from datetime import datetime, timezone

SITE = "https://api.lever.co/v0/postings/OpenPayd?mode=json"
EXTRA_HOSTS = []


def _plain(value):
    value = value or ""
    value = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/ul|/ol|/h[1-6]|/section|/article|/blockquote)\s*/?\s*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    lines = []
    for line in value.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def _description(posting):
    sections = [_plain(posting.get("description"))]
    for section in posting.get("lists") or []:
        heading = _plain(section.get("text"))
        content = _plain(section.get("content"))
        sections.append("\n".join(part for part in (heading, content) if part))
    sections.append(_plain(posting.get("additional")))
    return "\n\n".join(section for section in sections if section)


def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))
    for posting in postings:
        categories = posting.get("categories") or {}
        created = posting.get("createdAt")
        posted_at = None
        if created is not None:
            posted_at = datetime.fromtimestamp(
                created / 1000, tz=timezone.utc
            ).isoformat().replace("+00:00", "Z")

        record = {
            "url": posting["hostedUrl"],
            "title": posting["text"],
            "company": "OpenPayd",
            "location": categories["location"],
            "description": _description(posting),
            "source": SITE,
        }

        optional = {
            "locations": categories.get("allLocations"),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("department"),
            "posted_at": posted_at,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        for key, value in optional.items():
            if value is not None and value != "":
                record[key] = value

        yield record
