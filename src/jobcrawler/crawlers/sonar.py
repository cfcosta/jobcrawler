
import json
import re
from datetime import datetime, timezone
from html import unescape

SITE = "https://api.lever.co/v0/postings/sonarsource?mode=json"
EXTRA_HOSTS = []


def _text(value):
    if not value:
        return None
    text = re.sub(
        r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>",
        "",
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<li\b[^>]*>", "\n- ", text, flags=re.IGNORECASE)
    text = re.sub(
        r"</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|"
        r"figcaption|footer|h[1-6]|header|hr|main|ol|p|pre|section|"
        r"table|tr|ul)\b[^>]*>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"</li\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _body(posting):
    sections = []
    description = posting.get("description")
    if description:
        sections.append(description)

    for section in posting.get("lists") or []:
        heading = section.get("text")
        content = section.get("content")
        if heading:
            sections.append("<h2>" + heading + "</h2>" + (content or ""))
        elif content:
            sections.append(content)

    additional = posting.get("additional")
    if additional:
        sections.append(additional)

    return _text("\n".join(sections))


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
        salary_range = posting.get("salaryRange") or {}

        fields = {
            "url": posting.get("hostedUrl"),
            "title": posting.get("text"),
            "company": "SonarSource",
            "location": categories.get("location"),
            "locations": categories.get("allLocations"),
            "description": _body(posting),
            "source": SITE,
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("department"),
            "posted_at": _posted_at(posting.get("createdAt")),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "salary_min": salary_range.get("min"),
            "salary_max": salary_range.get("max"),
            "salary_currency": salary_range.get("currency"),
            "salary_period": salary_range.get("interval"),
        }
        yield {
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        }
