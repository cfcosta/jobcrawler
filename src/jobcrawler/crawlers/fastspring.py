
import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/fastspring/jobs?content=true"
EXTRA_HOSTS = []
API_URL = SITE + "&pay_transparency=true"


def _description(value):
    text = html.unescape(value)
    text = re.sub(
        r"(?i)<\s*(?:br|hr)\s*/?\s*>|</?\s*(?:p|div|h[1-6]|li|ul|ol|"
        r"section|article|blockquote|pre|table|tr|td|th)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    lines = []
    for line in text.splitlines():
        cleaned = " ".join(html.unescape(line).replace("\xa0", " ").split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _compensation(job):
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

    result = {"salary_currency": next(iter(currencies))}
    if minimums:
        result["salary_min"] = min(minimums)
    if maximums:
        result["salary_max"] = max(maximums)
    return result


def crawl(ctx):
    data = json.loads(ctx.fetch(API_URL))

    for item in data.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        offices = [
            office["name"]
            for office in item.get("offices") or []
            if office.get("name")
        ]
        departments = [
            department["name"]
            for department in item.get("departments") or []
            if department.get("name")
        ]

        fields = {
            "url": item["absolute_url"],
            "title": item["title"],
            "company": "FastSpring",
            "location": location,
            "description": _description(item.get("content") or ""),
            "source": "greenhouse",
            "external_id": str(item["id"]),
            "locations": offices or None,
            "department": ", ".join(departments) if departments else None,
            "posted_at": item.get("first_published"),
            "expires_at": item.get("application_deadline"),
        }
        fields.update(_compensation(item))
        yield {key: value for key, value in fields.items() if value is not None}
