
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/pulley/jobs?content=true"
EXTRA_HOSTS = []


def _text(value):
    value = html.unescape(value or "")
    value = re.sub(r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)>", "", value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(?:p|div|li|ul|ol|h[1-6]|section|table|tr)>", "\n", value)
    value = re.sub(r"(?s)<[^>]+>", "", value)
    value = html.unescape(value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"[ \t]{2,}", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _salary_fields(item):
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
    result = {"salary_currency": next(iter(currencies))}
    if minimums:
        result["salary_min"] = min(minimums)
    if maximums:
        result["salary_max"] = max(maximums)

    periods = {
        value.get("pay_period")
        for value in ranges
        if value.get("pay_period")
    }
    if len(periods) == 1:
        result["salary_period"] = next(iter(periods))
    return result


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))
    for item in data.get("jobs", []):
        primary_location = (item.get("location") or {}).get("name")
        offices = [
            office.get("name")
            for office in item.get("offices", [])
            if office.get("name")
        ]
        departments = [
            department.get("name")
            for department in item.get("departments", [])
            if department.get("name")
        ]

        job = {
            "url": item.get("absolute_url"),
            "title": item.get("title"),
            "company": "Pulley",
            "location": primary_location,
            "description": _text(item.get("content")),
            "source": SITE,
            "external_id": str(item["id"]) if item.get("id") is not None else None,
        }
        if offices:
            job["locations"] = offices
        if departments:
            job["department"] = ", ".join(departments)
        job.update(_salary_fields(item))
        yield {key: value for key, value in job.items() if value is not None}


