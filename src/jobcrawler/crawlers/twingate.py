
import html
import json
import re
from datetime import datetime, timezone



SITE = "https://api.lever.co/v0/postings/twingate?mode=json"
EXTRA_HOSTS = []


def _plain(value):
    if not value:
        return None

    text = html.unescape(value)
    text = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/ul|/ol|/h[1-6]|/tr|/section)\s*/?\s*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines) or None


def _description(posting):
    sections = []

    intro = posting.get("descriptionPlain") or _plain(posting.get("description"))
    if intro:
        sections.append(intro.strip())

    for section in posting.get("lists") or []:
        heading = _plain(section.get("text"))
        content = _plain(section.get("content"))
        section_text = "\n".join(
            part for part in (heading, content) if part
        )
        if section_text:
            sections.append(section_text)

    closing = posting.get("additionalPlain") or _plain(posting.get("additional"))
    if closing:
        sections.append(closing.strip())

    return "\n\n".join(sections)


def _timestamp(epoch_ms):
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, timezone.utc).isoformat()


def crawl(ctx):
    response = ctx.fetch(SITE)
    try:
        postings = response.json()
    except AttributeError:
        postings = json.loads(response)

    for posting in postings:
        categories = posting.get("categories") or {}
        salary_range = posting.get("salaryRange") or {}

        fields = {
            "url": posting.get("hostedUrl"),
            "title": posting.get("text"),
            "company": "Twingate",
            "source": "Lever",
            "location": categories.get("location"),
            "description": _description(posting),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("department") or categories.get("team"),
            "updated_at": _timestamp(posting.get("createdAt")),
            "salary_min": salary_range.get("min"),
            "salary_max": salary_range.get("max"),
            "salary_currency": salary_range.get("currency"),
            "salary_period": salary_range.get("interval"),
        }

        locations = categories.get("allLocations")
        if isinstance(locations, list) and locations:
            fields["locations"] = locations

        yield {
            key: value
            for key, value in fields.items()
            if value is not None
        }
