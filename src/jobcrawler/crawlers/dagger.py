
import html
import json
import re
from jobcrawler.runtime.contract import Job


SITE = 'https://boards-api.greenhouse.io/v1/boards/dagger/jobs?content=true'
EXTRA_HOSTS = []


_BLOCK_TAGS = re.compile(
    r"</?(?:address|article|aside|blockquote|br|div|footer|h[1-6]|header|hr|"
    r"li|main|ol|p|pre|section|table|tr|ul)(?:\\s[^>]*)?>",
    re.IGNORECASE,
)
_ALL_TAGS = re.compile(r"<[^>]+>")
_BLANK_LINES = re.compile(r"\\n{3,}")


def _plain_text(value):
    text = html.unescape(value or "")
    text = _BLOCK_TAGS.sub("\\n", text)
    text = _ALL_TAGS.sub("", text)
    lines = []
    for line in html.unescape(text).splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return _BLANK_LINES.sub("\\n\\n", "\\n".join(lines)).strip()


def _salary_fields(job):
    ranges = job.get("pay_input_ranges") or []
    currencies = {
        item.get("currency_type")
        for item in ranges
        if item.get("currency_type")
    }
    if not ranges or len(currencies) != 1:
        return {}

    result = {"salary_currency": next(iter(currencies))}
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
    response = ctx.fetch(SITE + "&pay_transparency=true")
    payload = json.loads(response)

    for item in payload.get("jobs", []):
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
        url = item.get("absolute_url")

        fields = {
            "url": url,
            "title": item.get("title"),
            "company": "Dagger",
            "location": location,
            "description": _plain_text(item.get("content")),
            "source": SITE,
            "external_id": str(item["id"]) if item.get("id") is not None else None,
            "apply_url": url,
        }
        if offices:
            fields["locations"] = offices
        if departments:
            fields["department"] = ", ".join(departments)
        fields.update(_salary_fields(item))

        yield Job(**{key: value for key, value in fields.items() if value is not None})
