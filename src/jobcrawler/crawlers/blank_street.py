import json
import re
from html import unescape


SITE = "https://boards-api.greenhouse.io/v1/boards/blankstreet/jobs?content=true"
EXTRA_HOSTS = []
_DISCOVERY_URL = SITE + "&pay_transparency=true"


def _html_to_text(value):
    value = unescape(value or "")
    value = re.sub(
        r"(?is)<(?:script|style|noscript|template)\b[^>]*>.*?</(?:script|style|noscript|template)\s*>",
        " ",
        value,
    )
    value = re.sub(
        r"(?i)<\s*(?:br|hr)\s*/?\s*>|</?\s*(?:address|article|blockquote|div|dl|dt|dd|figcaption|figure|footer|h[1-6]|header|li|main|ol|p|pre|section|table|tbody|td|th|thead|tr|ul)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = unescape(value)

    lines = []
    for line in value.splitlines():
        normalized = " ".join(line.split())
        if normalized:
            lines.append(normalized)
    return "\n".join(lines)


def _pay_fields(ranges):
    ranges = [item for item in (ranges or []) if isinstance(item, dict)]
    currencies = {
        item.get("currency_type")
        for item in ranges
        if item.get("currency_type")
    }
    if not ranges or len(currencies) != 1:
        return {}

    minimums = [
        item["min_cents"] / 100
        for item in ranges
        if item.get("min_cents") is not None
    ]
    maximums = [
        item["max_cents"] / 100
        for item in ranges
        if item.get("max_cents") is not None
    ]

    fields = {"salary_currency": next(iter(currencies))}
    if minimums:
        fields["salary_min"] = min(minimums)
    if maximums:
        fields["salary_max"] = max(maximums)
    return fields


def crawl(ctx):
    response = ctx.fetch(_DISCOVERY_URL)
    payload = json.loads(response)

    for record in payload.get("jobs", []):
        title = record.get("title")
        url = record.get("absolute_url")
        location = (record.get("location") or {}).get("name")
        description = _html_to_text(record.get("content"))

        if not title or not url or not location or not description:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Blank Street",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(record["id"]),
            "apply_url": url,
        }

        departments = [
            item.get("name")
            for item in record.get("departments", [])
            if item.get("name")
        ]
        if departments:
            fields["department"] = ", ".join(departments)

        offices = [
            item.get("name")
            for item in record.get("offices", [])
            if item.get("name")
        ]
        if offices:
            fields["locations"] = offices

        if record.get("first_published"):
            fields["posted_at"] = record["first_published"]
        if record.get("application_deadline"):
            fields["expires_at"] = record["application_deadline"]

        fields.update(_pay_fields(record.get("pay_input_ranges")))
        yield fields
