
import json
import re
from datetime import datetime, timezone
from html import unescape


SITE = "https://api.lever.co/v0/postings/finch?mode=json"
EXTRA_HOSTS = []
COMPANY = "Finch"


def _plain(value):
    if not value:
        return None
    text = re.sub(
        r"</?(?:br|div|p|li|ul|ol|h[1-6]|blockquote|section|article|table|tr)\b[^>]*>",
        "\n",
        value,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line) or None


def _description(posting):
    sections = []

    intro = _plain(posting.get("description"))
    if intro:
        sections.append(intro)

    for section in posting.get("lists") or []:
        content = _plain(section.get("content"))
        if not content:
            continue
        heading = section.get("text")
        if heading:
            sections.append(f"{heading}\n{content}")
        else:
            sections.append(content)

    additional = _plain(posting.get("additional"))
    if additional:
        sections.append(additional)

    return "\n\n".join(sections)


def _posted_at(epoch_ms):
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp(
        epoch_ms / 1000, tz=timezone.utc
    ).isoformat().replace("+00:00", "Z")


def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))

    for posting in postings:
        categories = posting.get("categories") or {}
        all_locations = categories.get("allLocations")
        salary_range = posting.get("salaryRange") or {}

        fields = {
            "url": posting.get("hostedUrl"),
            "source": "Lever",
            "title": posting.get("text"),
            "company": COMPANY,
            "location": categories.get("location"),
            "description": _description(posting),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("team"),
            "posted_at": _posted_at(posting.get("createdAt")),
            "locations": all_locations if isinstance(all_locations, list) else None,
            "salary_min": salary_range.get("min"),
            "salary_max": salary_range.get("max"),
            "salary_currency": salary_range.get("currency"),
            "salary_period": salary_range.get("interval"),
            "salary": posting.get("salaryDescriptionPlain"),
        }

        yield {key: value for key, value in fields.items() if value is not None}
