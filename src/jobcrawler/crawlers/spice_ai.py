
import json
import re
from datetime import datetime, timezone
from html import unescape

SITE = "https://api.lever.co/v0/postings/spiceai?mode=json"
EXTRA_HOSTS = []


def _text(value):
    if not value:
        return None
    text = unescape(str(value))
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(?:p|div|li|h[1-6]|ul|ol)>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None


def _description(posting):
    parts = []

    intro = _text(posting.get("description"))
    if intro:
        parts.append(intro)

    for section in posting.get("lists") or []:
        label = _text(section.get("text"))
        content = _text(section.get("content"))
        section_text = "\n".join(
            part for part in (label, content) if part
        )
        if section_text:
            parts.append(section_text)

    additional = _text(posting.get("additional"))
    if additional:
        parts.append(additional)

    return "\n\n".join(parts)


def _posted_at(value):
    if value is None:
        return None
    return datetime.fromtimestamp(
        value / 1000, tz=timezone.utc
    ).isoformat()


def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))

    for posting in postings:
        categories = posting.get("categories") or {}
        hosted_url = posting.get("hostedUrl")

        job = {
            "url": hosted_url,
            "title": posting.get("text"),
            "company": "Spice AI",
            "location": categories.get("location"),
            "description": _description(posting),
            "source": SITE,
        }

        salary_range = posting.get("salaryRange") or {}

        optional = {
            "locations": categories.get("allLocations"),
            "workplace_type": posting.get("workplaceType"),
            "employment_type": categories.get("commitment"),
            "department": categories.get("team") or categories.get("department"),
            "posted_at": _posted_at(posting.get("createdAt")),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl") or hosted_url,
            "salary": posting.get("salaryDescriptionPlain"),
            "salary_min": salary_range.get("min"),
            "salary_max": salary_range.get("max"),
            "salary_currency": salary_range.get("currency"),
            "salary_period": salary_range.get("interval"),
        }
        for key, value in optional.items():
            if value:
                job[key] = value

        yield job
