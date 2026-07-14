import html
import json
import re
from datetime import datetime, timezone


SITE = "https://api.lever.co/v0/postings/truv?mode=json"
EXTRA_HOSTS = []


def _plain(value):
    if not value:
        return None

    text = re.sub(
        r"(?i)</?(?:br|p|div|li|h[1-6]|section|tr|ul|ol)(?:\s[^>]*)?>",
        "\\n",
        value,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip() or None


def _description(posting):
    sections = []

    intro = posting.get("descriptionPlain") or _plain(
        posting.get("description")
    )
    if intro and intro.strip():
        sections.append(intro.strip())

    for section in posting.get("lists") or []:
        content = _plain(section.get("content"))
        label = (section.get("text") or "").strip()
        if content:
            sections.append(label + "\n" + content if label else content)

    additional = posting.get("additionalPlain") or _plain(
        posting.get("additional")
    )
    if additional and additional.strip():
        sections.append(additional.strip())

    return "\n\n".join(sections)


def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))

    for posting in postings:
        categories = posting.get("categories") or {}
        all_locations = categories.get("allLocations")
        salary_range = posting.get("salaryRange") or {}

        fields = {
            "url": posting["hostedUrl"],
            "title": posting["text"],
            "company": "Truv",
            "location": categories.get("location"),
            "description": _description(posting),
            "source": SITE,
        }

        if all_locations:
            fields["locations"] = all_locations
        if posting.get("workplaceType"):
            fields["workplace_type"] = posting["workplaceType"]
        if categories.get("team"):
            fields["department"] = categories["team"]
        if posting.get("id"):
            fields["external_id"] = posting["id"]
        if posting.get("applyUrl"):
            fields["apply_url"] = posting["applyUrl"]

        created_at = posting.get("createdAt")
        if created_at is not None:
            fields["posted_at"] = datetime.fromtimestamp(
                created_at / 1000, tz=timezone.utc
            ).isoformat()

        if salary_range.get("min") is not None:
            fields["salary_min"] = salary_range["min"]
        if salary_range.get("max") is not None:
            fields["salary_max"] = salary_range["max"]
        if salary_range.get("currency"):
            fields["salary_currency"] = salary_range["currency"]
        if salary_range.get("interval"):
            fields["salary_period"] = salary_range["interval"]

        salary_text = posting.get("salaryDescriptionPlain")
        if salary_text and salary_text.strip():
            fields["salary"] = salary_text

        yield fields
