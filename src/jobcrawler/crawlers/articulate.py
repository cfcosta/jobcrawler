
import html
import json
import re
from datetime import datetime, timezone

SITE = "https://api.lever.co/v0/postings/articulate?mode=json"
EXTRA_HOSTS = []


def _text(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)<\s*li(?:\s[^>]*)?>", "\n- ", value)
    value = re.sub(
        r"(?i)</?\s*(?:div|p|ul|ol|h[1-6])(?:\s[^>]*)?>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    return value or None


def _description(posting):
    sections = []

    intro = _text(posting.get("description"))
    if intro:
        sections.append(intro)

    for item in posting.get("lists") or []:
        label = _text(item.get("text"))
        content = _text(item.get("content"))
        section = "\n".join(part for part in (label, content) if part)
        if section:
            sections.append(section)

    additional = _text(posting.get("additional"))
    if additional:
        sections.append(additional)

    return "\n\n".join(sections)


def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))

    for posting in postings:
        categories = posting.get("categories") or {}
        salary_range = posting.get("salaryRange") or {}
        created_at = posting.get("createdAt")
        posted_at = None
        if isinstance(created_at, (int, float)):
            posted_at = datetime.fromtimestamp(
                created_at / 1000, timezone.utc
            ).isoformat()

        values = {
            "url": posting.get("hostedUrl"),
            "title": posting.get("text"),
            "company": "Articulate",
            "location": categories.get("location"),
            "locations": categories.get("allLocations"),
            "description": _description(posting),
            "source": SITE,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "department": categories.get("team"),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "posted_at": posted_at,
            "salary": posting.get("salaryDescriptionPlain"),
            "salary_min": salary_range.get("min"),
            "salary_max": salary_range.get("max"),
            "salary_currency": salary_range.get("currency"),
            "salary_period": salary_range.get("interval"),
        }
        yield {
            key: value
            for key, value in values.items()
            if value is not None and value != []
        }
