import json
import re
from html import unescape


SITE = "https://apply.workable.com/api/v1/widget/accounts/tyk-technologies"
EXTRA_HOSTS = []


def _html_to_text(value):
    value = re.sub(
        r"(?i)<\s*(br|/p|/div|/li|/ul|/ol|/h[1-6]|/section|/article|/tr)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = unescape(value)
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _location_text(item):
    parts = []
    for key in ("city", "region", "country"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            value = value.strip()
            if value not in parts:
                parts.append(value)
    return ", ".join(parts)


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE + "?details=true"))
    company = payload.get("name") or "Tyk Technologies"

    for item in payload.get("jobs", []):
        locations = [
            text
            for text in (_location_text(entry) for entry in item.get("locations", []))
            if text
        ]
        description_html = item.get("description")
        fields = {
            "url": item.get("url"),
            "title": item.get("title"),
            "company": company,
            "location": locations[0] if locations else None,
            "description": _html_to_text(description_html) if description_html else None,
            "source": "Workable",
            "external_id": item.get("shortcode"),
            "apply_url": item.get("application_url"),
            "department": item.get("department"),
            "employment_type": item.get("employment_type"),
            "posted_at": item.get("published_on"),
        }
        if locations:
            fields["locations"] = locations
        if item.get("telecommuting") is True:
            fields["workplace_type"] = "remote"

        yield {
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        }
