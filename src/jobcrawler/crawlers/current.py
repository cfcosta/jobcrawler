import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/current81/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"<\s*(?:br|/p|/div|/li|/ul|/ol|/h[1-6]|/tr|/table)\s*/?\s*>",
        "\n",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    lines = [
        re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        for line in value.split("\n")
    ]
    return "\n".join(line for line in lines if line)


def _pay_fields(item):
    ranges = item.get("pay_input_ranges") or []
    currencies = {
        value.get("currency_type")
        for value in ranges
        if value.get("currency_type")
    }
    if not ranges or len(currencies) != 1:
        return {}

    minimums = [
        value["min_cents"] / 100
        for value in ranges
        if value.get("min_cents") is not None
    ]
    maximums = [
        value["max_cents"] / 100
        for value in ranges
        if value.get("max_cents") is not None
    ]
    fields = {"salary_currency": next(iter(currencies))}
    if minimums:
        fields["salary_min"] = min(minimums)
    if maximums:
        fields["salary_max"] = max(maximums)
    return fields


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))

    for item in data.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        offices = [
            office["name"]
            for office in item.get("offices", [])
            if office.get("name")
        ]
        departments = [
            department["name"]
            for department in item.get("departments", [])
            if department.get("name")
        ]

        fields = {
            "url": item.get("absolute_url"),
            "title": item.get("title"),
            "company": "Current",
            "location": location,
            "description": _plain_text(item.get("content")),
            "source": SITE,
            "external_id": str(item["id"]) if item.get("id") is not None else None,
            "posted_at": item.get("first_published"),
            "expires_at": item.get("application_deadline"),
            "department": ", ".join(departments) if departments else None,
            "locations": offices or None,
        }
        fields.update(_pay_fields(item))
        yield {key: value for key, value in fields.items() if value is not None}
