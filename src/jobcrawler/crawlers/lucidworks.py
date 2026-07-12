
import json
import re
from datetime import datetime, timezone
from html import unescape

SITE = "https://api.lever.co/v0/postings/lucidworks?mode=json"
EXTRA_HOSTS = []


def _html_text(value):
    if not value:
        return None

    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    text = re.sub(r"(?i)<\s*li(?:\s[^>]*)?>", "\n- ", text)
    text = re.sub(
        r"(?i)</?\s*(?:address|article|aside|blockquote|div|footer|h[1-6]|"
        r"header|hr|main|nav|ol|p|pre|section|table|tr|ul)(?:\s[^>]*)?>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]*>", "", text)
    text = unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None


def _full_description(posting):
    sections = []

    intro = _html_text(posting.get("description"))
    if intro:
        sections.append(intro)

    for section in posting.get("lists") or []:
        heading = section.get("text")
        content = _html_text(section.get("content"))
        if heading and content:
            sections.append("%s\n%s" % (heading.strip(), content))
        elif heading:
            sections.append(heading.strip())
        elif content:
            sections.append(content)

    additional = _html_text(posting.get("additional"))
    if additional:
        sections.append(additional)

    return "\n\n".join(sections).strip()


def _posted_at(epoch_ms):
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp(
        epoch_ms / 1000.0, tz=timezone.utc
    ).isoformat()


def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))

    for posting in postings:
        categories = posting.get("categories") or {}
        salary_range = posting.get("salaryRange") or {}

        data = {
            "source": SITE,
            "url": posting.get("hostedUrl"),
            "title": posting.get("text"),
            "company": "Lucidworks",
            "location": categories.get("location"),
            "locations": categories.get("allLocations"),
            "description": _full_description(posting),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("department") or categories.get("team"),
            "posted_at": _posted_at(posting.get("createdAt")),
            "salary_min": salary_range.get("min"),
            "salary_max": salary_range.get("max"),
            "salary_currency": salary_range.get("currency"),
            "salary_period": salary_range.get("interval"),
            "salary": posting.get("salaryDescriptionPlain"),
        }

        yield {
            key: value
            for key, value in data.items()
            if value is not None and value != ""
        }
