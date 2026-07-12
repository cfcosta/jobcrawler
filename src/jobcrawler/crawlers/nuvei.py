
import json
import re
from html import unescape


SITE = "https://apply.workable.com/api/v1/widget/accounts/nuvei?details=true"
EXTRA_HOSTS = []


def _html_to_text(value):
    value = unescape(value or "")
    value = re.sub(
        r"(?i)<\s*(?:br|p|div|li|ul|ol|h[1-6]|section|article|blockquote|tr)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(
        r"(?i)</\s*(?:p|div|li|ul|ol|h[1-6]|section|article|blockquote|tr)\s*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    lines = (" ".join(line.split()) for line in value.splitlines())
    return "\n".join(line for line in lines if line)


def _location(item):
    parts = []
    for key in ("city", "region", "country"):
        value = item.get(key)
        if value and value not in parts:
            parts.append(value)
    return ", ".join(parts)


def crawl(ctx):
    response = ctx.fetch(SITE)
    payload = response if isinstance(response, str) else response.text
    board = json.loads(payload)

    for item in board.get("jobs", []):
        structured_locations = item.get("locations") or []
        locations = [
            location
            for location in (_location(entry) for entry in structured_locations)
            if location
        ]

        primary = _location({
            "city": item.get("city"),
            "region": item.get("state"),
            "country": item.get("country"),
        })
        if not primary and locations:
            primary = locations[0]

        fields = {
            "source": SITE,
            "url": item.get("url") or item.get("shortlink"),
            "title": item.get("title"),
            "company": "Nuvei",
            "location": primary,
            "locations": locations or None,
            "description": _html_to_text(item.get("description")),
            "department": item.get("department"),
            "employment_type": item.get("employment_type"),
            "workplace_type": "remote" if item.get("telecommuting") is True else None,
            "posted_at": item.get("published_on"),
            "external_id": item.get("shortcode"),
            "apply_url": item.get("application_url"),
        }
        yield {
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        }
