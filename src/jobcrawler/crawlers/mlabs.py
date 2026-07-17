
import html
import json
import re

from jobcrawler.runtime.contract import Job

SITE = "https://apply.workable.com/api/v1/widget/accounts/mlabs"
EXTRA_HOSTS = []


def _plain_text(value):
    text = value or ""
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(
        r"(?i)</?(?:p|div|li|ul|ol|h[1-6]|section|article|blockquote|tr|table)[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _location_name(location):
    parts = []
    for key in ("city", "region", "country"):
        value = location.get(key)
        if value and value not in parts:
            parts.append(value)
    return ", ".join(parts)


def crawl(ctx):
    response = ctx.fetch(SITE + "?details=true")
    raw = response.text if hasattr(response, "text") else response
    data = json.loads(raw)

    for item in data.get("jobs", []):
        locations = []
        for value in item.get("locations", []):
            name = _location_name(value)
            if name:
                locations.append(name)

        primary = _location_name({
            "city": item.get("city"),
            "region": item.get("state"),
            "country": item.get("country"),
        })
        if not primary and locations:
            primary = locations[0]

        fields = {
            "url": item.get("url"),
            "title": item.get("title"),
            "company": "MLabs",
            "source": "Workable",
            "location": primary,
            "locations": locations or None,
            "description": _plain_text(item.get("description")),
            "department": item.get("department") or None,
            "posted_at": item.get("published_on") or None,
            "external_id": item.get("shortcode") or item.get("code") or None,
            "apply_url": item.get("application_url") or None,
            "workplace_type": "remote" if item.get("telecommuting") is True else None,
            "employment_type": item.get("employment_type") or None,
        }
        yield Job(**{key: value for key, value in fields.items() if value is not None})
