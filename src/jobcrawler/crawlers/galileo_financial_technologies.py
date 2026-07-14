import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/galileofinancialtechnologies/jobs?content=true"
EXTRA_HOSTS = []

DISCOVERY_URL = SITE + "&pay_transparency=true"
COMPANY = "Galileo Financial Technologies"


def _plain_text(value):
    decoded = html.unescape(value or "")
    decoded = re.sub(
        r"(?is)<(script|style)\b[^>]*>.*?</\1\s*>",
        "",
        decoded,
    )
    decoded = re.sub(
        r"(?i)<\s*(br|hr)\s*/?\s*>|</?\s*(address|article|aside|blockquote|div|footer|h[1-6]|header|li|main|ol|p|pre|section|table|td|th|tr|ul)\b[^>]*>",
        "\n",
        decoded,
    )
    decoded = re.sub(r"(?s)<[^>]+>", "", decoded)
    decoded = html.unescape(decoded)

    lines = []
    for line in decoded.splitlines():
        normalized = " ".join(line.split())
        if normalized and (not lines or normalized != lines[-1]):
            lines.append(normalized)
    return "\n".join(lines)


def _pay_fields(job):
    ranges = job.get("pay_input_ranges") or []
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
    payload = json.loads(ctx.fetch(DISCOVERY_URL))

    for item in payload.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        description = _plain_text(item.get("content"))
        if not item.get("title") or not location or not description:
            continue

        fields = {
            "url": item["absolute_url"],
            "title": item["title"],
            "company": COMPANY,
            "location": location,
            "description": description,
            "source": "Greenhouse",
            "external_id": str(item["id"]),
        }

        offices = [
            office["name"]
            for office in item.get("offices", [])
            if office.get("name")
        ]
        if offices:
            fields["locations"] = offices

        departments = [
            department["name"]
            for department in item.get("departments", [])
            if department.get("name")
        ]
        if departments:
            fields["department"] = ", ".join(departments)

        if item.get("first_published"):
            fields["posted_at"] = item["first_published"]
        if item.get("application_deadline"):
            fields["expires_at"] = item["application_deadline"]

        fields.update(_pay_fields(item))
        yield fields
