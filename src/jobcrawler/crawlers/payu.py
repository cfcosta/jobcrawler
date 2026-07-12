import json
import re
from datetime import datetime, timezone
from html import unescape


SITE = "https://api.lever.co/v0/postings/payugpo?mode=json"
EXTRA_HOSTS = []
COMPANY = "PayU GPO EMEA"


def _text(value):
    if not value:
        return None
    value = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/ul|/ol|/h[1-6])\s*/?\s*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = unescape(value)
    lines = [" ".join(line.split()) for line in value.splitlines()]
    result = "\n".join(line for line in lines if line)
    return result or None


def _plain_or_html(plain, html_value):
    if plain and plain.strip():
        return plain.strip()
    return _text(html_value)


def _description(posting):
    parts = []
    intro = _plain_or_html(
        posting.get("descriptionPlain"),
        posting.get("description"),
    )
    if intro:
        parts.append(intro)

    for section in posting.get("lists") or []:
        heading = (section.get("text") or "").strip()
        content = _text(section.get("content"))
        section_parts = [part for part in (heading, content) if part]
        if section_parts:
            parts.append("\n".join(section_parts))

    additional = _plain_or_html(
        posting.get("additionalPlain"),
        posting.get("additional"),
    )
    if additional:
        parts.append(additional)

    return "\n\n".join(parts).strip()


def _posted_at(value):
    if value is None:
        return None
    return datetime.fromtimestamp(
        value / 1000,
        tz=timezone.utc,
    ).isoformat()


def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))
    for posting in postings:
        categories = posting.get("categories") or {}

        yield dict(
            url=posting.get("hostedUrl"),
            title=posting.get("text"),
            company=COMPANY,
            location=categories.get("location"),
            locations=categories.get("allLocations") or None,
            description=_description(posting),
            source=SITE,
            employment_type=categories.get("commitment"),
            workplace_type=posting.get("workplaceType"),
            department=categories.get("department"),
            posted_at=_posted_at(posting.get("createdAt")),
            external_id=posting.get("id"),
            apply_url=posting.get("applyUrl"),
        )
