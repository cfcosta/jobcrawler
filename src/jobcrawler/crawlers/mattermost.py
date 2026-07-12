
import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/mattermost/jobs?content=true"
EXTRA_HOSTS = []


def clean_description(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)<\s*(?:br|p|div|li|h[1-6]|tr|section|article|blockquote|pre|hr)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(
        r"(?i)</\s*(?:p|div|li|h[1-6]|tr|section|article|blockquote|pre|ul|ol|table)\s*>",
        "\n",
        value,
    )
    value = re.sub(r"(?s)<[^>]+>", "", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def pay_fields(item):
    ranges = item.get("pay_input_ranges") or []
    usable = [
        value for value in ranges
        if value.get("currency_type")
        and (
            value.get("min_cents") is not None
            or value.get("max_cents") is not None
        )
    ]
    currencies = {value["currency_type"] for value in usable}
    if not usable or len(currencies) != 1:
        return {}

    minimums = [
        value["min_cents"] / 100
        for value in usable
        if value.get("min_cents") is not None
    ]
    maximums = [
        value["max_cents"] / 100
        for value in usable
        if value.get("max_cents") is not None
    ]
    periods = {
        value.get("interval") or value.get("pay_period")
        for value in usable
        if value.get("interval") or value.get("pay_period")
    }

    result = {"salary_currency": next(iter(currencies))}
    if minimums:
        result["salary_min"] = min(minimums)
    if maximums:
        result["salary_max"] = max(maximums)
    if len(periods) == 1:
        result["salary_period"] = next(iter(periods))
    return result


def crawl(ctx):
    response = ctx.fetch(SITE + "&pay_transparency=true")
    payload = json.loads(response)

    for item in payload.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        offices = [
            office["name"]
            for office in (item.get("offices") or [])
            if office.get("name")
        ]
        departments = [
            department["name"]
            for department in (item.get("departments") or [])
            if department.get("name")
        ]

        fields = {
            "url": item.get("absolute_url"),
            "apply_url": item.get("absolute_url"),
            "title": item.get("title"),
            "company": "Mattermost",
            "location": location,
            "description": clean_description(item.get("content")),
            "external_id": (
                str(item["id"]) if item.get("id") is not None else None
            ),
            "department": ", ".join(departments) if departments else None,
            "posted_at": item.get("first_published"),
            "expires_at": item.get("application_deadline"),
            "locations": offices or None,
            "source": "greenhouse",
        }
        fields.update(pay_fields(item))
        yield {
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        }
