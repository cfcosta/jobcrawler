
import html
import json
import re
from datetime import datetime, timezone


SITE = 'https://api.lever.co/v0/postings/crypto?mode=json'
EXTRA_HOSTS = []


def _html_text(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(
        r"(?i)<\s*(?:br|/div|/p|/li|/h[1-6]|/tr|/section|/article|/ul|/ol)\s*/?\s*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _description(posting):
    sections = []

    intro = posting.get("descriptionPlain")
    if intro:
        sections.append(intro.strip())
    else:
        intro = _html_text(posting.get("description"))
        if intro:
            sections.append(intro)

    for item in posting.get("lists") or []:
        heading = (item.get("text") or "").strip()
        content = _html_text(item.get("content"))
        section = "\n".join(part for part in (heading, content) if part)
        if section:
            sections.append(section)

    additional = posting.get("additionalPlain")
    if additional:
        sections.append(additional.strip())
    else:
        additional = _html_text(posting.get("additional"))
        if additional:
            sections.append(additional)

    return "\n\n".join(sections).strip()


def _posted_at(epoch_ms):
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp(
        epoch_ms / 1000, timezone.utc
    ).isoformat()


def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))

    for posting in postings:
        categories = posting.get("categories") or {}
        fields = {
            "source": SITE,
            "url": posting.get("hostedUrl"),
            "title": posting.get("text"),
            "company": "Crypto.com",
            "location": categories.get("location"),
            "description": _description(posting),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "posted_at": _posted_at(posting.get("createdAt")),
            "workplace_type": posting.get("workplaceType"),
            "employment_type": categories.get("commitment"),
            "department": categories.get("department"),
        }

        locations = categories.get("allLocations")
        if locations:
            fields["locations"] = locations

        yield {
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        }
