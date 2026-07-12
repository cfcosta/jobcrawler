import html
import json
import re

SITE = "https://apply.workable.com/api/v1/widget/accounts/elementio?details=true"
EXTRA_HOSTS = []


def _description(value):
    if not value:
        return None
    text = re.sub(
        r"(?i)<\s*(?:br|div|h[1-6]|li|ol|p|section|table|tr|ul)\b[^>]*>",
        "\n",
        value,
    )
    text = re.sub(
        r"(?i)</\s*(?:div|h[1-6]|li|ol|p|section|table|tr|ul)\s*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]*>", " ", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        normalized = " ".join(line.split())
        if normalized:
            lines.append(normalized)
    return "\n".join(lines) or None


def _location(item):
    parts = (item.get("city"), item.get("state"), item.get("country"))
    return ", ".join(part for part in parts if part) or None


def _structured_locations(job):
    locations = []
    for item in job.get("locations") or []:
        if isinstance(item, str):
            value = item.strip()
        elif isinstance(item, dict):
            value = _location(item)
        else:
            value = None
        if value:
            locations.append(value)
    return locations or None


def crawl(ctx):
    board = json.loads(ctx.fetch(SITE))

    for item in board.get("jobs") or []:
        fields = {
            "url": item.get("url") or item.get("shortlink"),
            "title": item.get("title"),
            "company": "Element",
            "location": _location(item),
            "locations": _structured_locations(item),
            "description": _description(item.get("description")),
            "source": SITE,
            "external_id": item.get("shortcode"),
            "apply_url": item.get("application_url"),
            "department": item.get("department"),
            "employment_type": item.get("employment_type"),
            "workplace_type": (
                "remote" if item.get("telecommuting") is True else None
            ),
            "posted_at": item.get("published_on") or item.get("created_at"),
        }
        yield {
            key: value for key, value in fields.items()
            if value is not None and value != ""
        }
