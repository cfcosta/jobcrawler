
import html
import json
import re
from jobcrawler.runtime.contract import Job

SITE = "https://boards-api.greenhouse.io/v1/boards/buftechnologies/jobs?content=true"
EXTRA_HOSTS = []


def _text(value):
    value = html.unescape(value or "")
    value = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1>", "", value)
    value = re.sub(
        r"(?i)<br\s*/?>|</p\s*>|</li\s*>|</h[1-6]\s*>|</div\s*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _salary(posting):
    ranges = posting.get("pay_input_ranges") or []
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

    periods = {
        item.get("interval")
        for item in ranges
        if item.get("interval")
    }
    if len(periods) == 1:
        result["salary_period"] = next(iter(periods))
    return result


def crawl(ctx):
    endpoint = SITE + "&pay_transparency=true"
    data = json.loads(ctx.fetch(endpoint))
    for posting in data.get("jobs", []):
        primary_location = (posting.get("location") or {}).get("name")
        offices = [
            office.get("name")
            for office in posting.get("offices", [])
            if office.get("name")
        ]
        departments = [
            department.get("name")
            for department in posting.get("departments", [])
            if department.get("name")
        ]

        kwargs = {
            "url": posting.get("absolute_url"),
            "title": posting.get("title"),
            "company": "Buf Technologies",
            "location": primary_location,
            "description": _text(posting.get("content")),
            "source": SITE,
            "external_id": (
                str(posting["id"]) if posting.get("id") is not None else None
            ),
            "locations": offices or None,
            "department": ", ".join(departments) if departments else None,
        }
        kwargs.update(_salary(posting))
        yield Job(**{
            key: value for key, value in kwargs.items()
            if value is not None
        })
