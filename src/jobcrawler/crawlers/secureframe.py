
from datetime import datetime, timezone
from html import unescape
import json
import re

SITE = "https://api.lever.co/v0/postings/secureframe?mode=json"
EXTRA_HOSTS = []


def _plain(value):
    if not value:
        return None
    text = unescape(value)
    text = re.sub(r"(?i)<br\s*/?>|</(?:p|div|li|ul|ol|h[1-6])\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    lines = []
    for line in text.replace("\r", "\n").splitlines():
        clean = " ".join(line.split())
        if clean:
            lines.append(clean)
    return "\n".join(lines) or None


def _full_description(posting):
    sections = []

    intro = posting.get("descriptionPlain") or _plain(posting.get("description"))
    if intro:
        sections.append(intro.strip())

    for item in posting.get("lists") or []:
        heading = item.get("text")
        body = _plain(item.get("content"))
        if heading and body:
            sections.append(heading.strip() + "\n" + body)
        elif body:
            sections.append(body)

    additional = posting.get("additionalPlain") or _plain(posting.get("additional"))
    if additional:
        sections.append(additional.strip())

    return "\n\n".join(sections)


def _posted_at(epoch_ms):
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).isoformat()


def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))

    for posting in postings:
        categories = posting.get("categories") or {}
        salary_range = posting.get("salaryRange") or {}
        salary_text = (
            posting.get("salaryDescriptionPlain")
            or _plain(posting.get("salaryDescription"))
        )

        fields = {
            "url": posting.get("hostedUrl"),
            "title": posting.get("text"),
            "company": "Secureframe",
            "location": categories.get("location"),
            "description": _full_description(posting),
            "source": SITE,
            "locations": categories.get("allLocations"),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("team"),
            "posted_at": _posted_at(posting.get("createdAt")),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "salary": salary_text,
            "salary_min": salary_range.get("min"),
            "salary_max": salary_range.get("max"),
            "salary_currency": salary_range.get("currency"),
            "salary_period": salary_range.get("interval"),
        }
        yield {key: value for key, value in fields.items() if value is not None}
