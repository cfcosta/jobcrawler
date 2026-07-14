import json
import re
from datetime import datetime, timezone
from html import unescape


SITE = 'https://api.lever.co/v0/postings/contentsquare?mode=json'
EXTRA_HOSTS = []
COMPANY = "Contentsquare"


def _plain_text(value):
    if not value:
        return ""
    text = re.sub(r"(?i)<br\s*/?>|</(?:div|p|li|h[1-6]|tr|section|ul|ol)>", "\n", value)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.replace("\xa0", " ").split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _description(posting):
    sections = []
    intro = _plain_text(posting.get("description"))
    if intro:
        sections.append(intro)

    for section in posting.get("lists") or []:
        heading = _plain_text(section.get("text"))
        content = _plain_text(section.get("content"))
        combined = "\n".join(part for part in (heading, content) if part)
        if combined:
            sections.append(combined)

    additional = _plain_text(posting.get("additional"))
    if additional:
        sections.append(additional)
    return "\n\n".join(sections)


def _posted_at(epoch_ms):
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).isoformat()


def crawl(ctx):
    response = ctx.fetch(SITE)
    body = response.text if hasattr(response, "text") else response
    postings = json.loads(body)

    for posting in postings:
        categories = posting.get("categories") or {}
        kwargs = {
            "url": posting["hostedUrl"],
            "title": posting["text"],
            "company": COMPANY,
            "location": categories.get("location"),
            "description": _description(posting),
            "external_id": posting.get("id"),
            "source": SITE,
        }

        optional = {
            "locations": categories.get("allLocations"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("team"),
            "posted_at": _posted_at(posting.get("createdAt")),
            "salary": posting.get("salaryDescriptionPlain"),
        }

        salary_range = posting.get("salaryRange") or {}
        optional.update({
            "salary_min": salary_range.get("min"),
            "salary_max": salary_range.get("max"),
            "salary_currency": salary_range.get("currency"),
            "salary_period": salary_range.get("interval"),
        })
        kwargs.update({
            key: value for key, value in optional.items()
            if value is not None and value != "" and value != []
        })
        yield kwargs
