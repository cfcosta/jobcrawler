
import json
import re
from html import unescape

SITE = "https://apply.workable.com/api/v1/widget/accounts/seedlegals"
EXTRA_HOSTS = []


def _html_to_text(value):
    text = value or ""
    text = re.sub(
        r"(?i)<\s*br\s*/?\s*>|</?\s*(?:p|div|section|article|h[1-6]|ul|ol|"
        r"blockquote|pre|table|thead|tbody|tfoot|tr|dl|dt|dd)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?i)<\s*li\b[^>]*>", "\n- ", text)
    text = re.sub(r"(?i)</\s*li\s*>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = unescape(text)

    lines = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def _format_location(item):
    parts = (item.get("city"), item.get("region"), item.get("country"))
    return ", ".join(part for part in parts if part)


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE + "?details=true"))

    for item in payload.get("jobs", []):
        locations = []
        for structured_location in item.get("locations", []):
            value = _format_location(structured_location)
            if value:
                locations.append(value)

        primary_location = ", ".join(
            value
            for value in (
                item.get("city"),
                item.get("state"),
                item.get("country"),
            )
            if value
        )

        fields = {
            "url": item.get("url"),
            "source": SITE,
            "title": item.get("title"),
            "company": "SeedLegals",
            "location": primary_location,
            "locations": locations or None,
            "description": _html_to_text(item.get("description")),
            "employment_type": item.get("employment_type"),
            "workplace_type": (
                "remote" if item.get("telecommuting") is True else None
            ),
            "department": item.get("department"),
            "posted_at": item.get("published_on"),
            "external_id": item.get("shortcode"),
            "apply_url": item.get("application_url"),
        }
        yield {
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        }
