
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/dagsterlabs/jobs?content=true"
EXTRA_HOSTS = []


def _text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"<(script|style)\b[^>]*>.*?</\1\s*>",
        "",
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    value = re.sub(
        r"</?(?:address|article|aside|blockquote|br|dd|div|dl|dt|fieldset|"
        r"figcaption|figure|footer|form|h[1-6]|header|hr|li|main|nav|ol|p|"
        r"pre|section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    lines = []
    for line in value.splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _names(items):
    return [
        item["name"]
        for item in (items or [])
        if isinstance(item, dict) and item.get("name")
    ]


def _compensation(job):
    ranges = job.get("pay_input_ranges") or []
    currencies = {
        item.get("currency_type")
        for item in ranges
        if isinstance(item, dict) and item.get("currency_type")
    }
    if not ranges or len(currencies) != 1:
        return {}

    minimums = [
        item["min_cents"] / 100
        for item in ranges
        if isinstance(item, dict) and item.get("min_cents") is not None
    ]
    maximums = [
        item["max_cents"] / 100
        for item in ranges
        if isinstance(item, dict) and item.get("max_cents") is not None
    ]
    result = {"salary_currency": next(iter(currencies))}
    if minimums:
        result["salary_min"] = min(minimums)
    if maximums:
        result["salary_max"] = max(maximums)

    periods = {
        item.get("pay_period")
        for item in ranges
        if isinstance(item, dict) and item.get("pay_period")
    }
    if len(periods) == 1:
        result["salary_period"] = next(iter(periods))
    return result


def crawl(ctx):
    response = ctx.fetch(SITE + "&pay_transparency=true")
    payload = json.loads(response)
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        raise ValueError("Greenhouse response does not contain a jobs list")

    for item in jobs:
        departments = _names(item.get("departments"))
        offices = _names(item.get("offices"))
        fields = {
            "url": item.get("absolute_url"),
            "title": item.get("title"),
            "company": "Dagster Labs",
            "location": (item.get("location") or {}).get("name"),
            "description": _text(item.get("content")),
            "external_id": str(item["id"]) if item.get("id") is not None else None,
            "department": ", ".join(departments) if departments else None,
            "locations": offices or None,
        }
        fields.update(_compensation(item))
        yield Job(**{key: value for key, value in fields.items() if value is not None})
