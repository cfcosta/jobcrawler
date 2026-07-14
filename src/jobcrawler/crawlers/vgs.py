
import html
import json
import re
from datetime import datetime, timezone

SITE = "https://api.lever.co/v0/postings/verygoodsecurity?mode=json"
EXTRA_HOSTS = []


def _text(value):
    if not value:
        return None
    value = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|"
        r"figcaption|figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|"
        r"pre|section|table|tr|ul)[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    lines = [" ".join(line.split()) for line in value.splitlines()]
    cleaned = "\n".join(line for line in lines if line)
    return cleaned or None


def _posted_at(value):
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()


def crawl(ctx):
    response = ctx.fetch(SITE)
    postings = response.json() if hasattr(response, "json") else json.loads(response)

    for posting in postings:
        categories = posting.get("categories") or {}

        parts = [posting.get("description")]
        parts.extend(
            section.get("content")
            for section in posting.get("lists") or []
            if section.get("content")
        )
        parts.append(posting.get("additional"))
        description = _text("\n\n".join(part for part in parts if part))

        salary_range = posting.get("salaryRange") or {}
        salary_text = _text(
            posting.get("salaryDescriptionPlain")
            or posting.get("salaryDescription")
        )

        fields = {
            "url": posting.get("hostedUrl"),
            "title": posting.get("text"),
            "company": "Very Good Security",
            "location": categories.get("location"),
            "locations": categories.get("allLocations"),
            "description": description,
            "source": SITE,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("department"),
            "posted_at": _posted_at(posting.get("createdAt")),
            "salary_min": salary_range.get("min"),
            "salary_max": salary_range.get("max"),
            "salary_currency": salary_range.get("currency"),
            "salary_period": salary_range.get("interval"),
            "salary": salary_text,
        }
        yield {key: value for key, value in fields.items() if value is not None}
