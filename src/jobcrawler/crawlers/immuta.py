
from datetime import datetime, timezone
from html import unescape
import json
import re

SITE = "https://api.lever.co/v0/postings/immuta?mode=json"
EXTRA_HOSTS = []


def _html_text(value):
    if not value:
        return None
    value = re.sub(
        r"(?i)</?(?:br|div|p|li|ul|ol|h[1-6]|section|article)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _posted_at(epoch_ms):
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp(
        epoch_ms / 1000, tz=timezone.utc
    ).isoformat()


def _description(posting):
    parts = []

    intro = posting.get("descriptionPlain")
    if intro and intro.strip():
        parts.append(intro.strip())

    for section in posting.get("lists") or []:
        heading = section.get("text")
        content = _html_text(section.get("content"))
        if heading and heading.strip():
            parts.append(heading.strip())
        if content:
            parts.append(content)

    additional = posting.get("additionalPlain")
    if additional and additional.strip():
        parts.append(additional.strip())

    return "\n\n".join(parts)


def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))

    for posting in postings:
        categories = posting.get("categories") or {}
        salary_range = posting.get("salaryRange") or {}

        fields = {
            "url": posting.get("hostedUrl"),
            "title": posting.get("text"),
            "company": "Immuta",
            "location": categories.get("location"),
            "description": _description(posting),
            "source": SITE,
            "locations": categories.get("allLocations"),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("team"),
            "posted_at": _posted_at(posting.get("createdAt")),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "salary": posting.get("salaryDescriptionPlain"),
            "salary_min": salary_range.get("min"),
            "salary_max": salary_range.get("max"),
            "salary_currency": salary_range.get("currency"),
            "salary_period": salary_range.get("interval"),
        }

        yield {
            key: value
            for key, value in fields.items()
            if value not in (None, "", [])
        }
