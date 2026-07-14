import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/streamnative/jobs?content=true"
EXTRA_HOSTS = []
DISCOVERY_URL = SITE + "&pay_transparency=true"

_BLOCK_TAGS = re.compile(
    r"</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|fieldset|"
    r"figcaption|figure|footer|form|h[1-6]|header|hr|li|main|nav|ol|p|"
    r"pre|section|table|td|th|tr|ul)\b[^>]*>",
    re.IGNORECASE,
)
_HIDDEN = re.compile(
    r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>",
    re.IGNORECASE | re.DOTALL,
)
_TAG = re.compile(r"<[^>]+>")


def _plain_text(value):
    text = html.unescape(value or "")
    text = _HIDDEN.sub("", text)
    text = _BLOCK_TAGS.sub("\n", text)
    text = _TAG.sub("", text)
    text = html.unescape(text)
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def _salary_fields(job):
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
    payload = json.loads(ctx.fetch(DISCOVERY_URL))
    for item in payload.get("jobs", []):
        title = item.get("title")
        url = item.get("absolute_url")
        location = (item.get("location") or {}).get("name")
        description = _plain_text(item.get("content"))
        if not all((title, url, location, description)):
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "StreamNative",
            "location": location,
            "description": description,
            "source": "Greenhouse",
            "external_id": (
                str(item["id"]) if item.get("id") is not None else None
            ),
            "posted_at": item.get("first_published"),
        }

        offices = [
            office.get("name")
            for office in item.get("offices", [])
            if office.get("name")
        ]
        if offices:
            fields["locations"] = offices

        departments = [
            department.get("name")
            for department in item.get("departments", [])
            if department.get("name")
        ]
        if departments:
            fields["department"] = ", ".join(departments)

        fields.update(_salary_fields(item))
        yield Job(**{
            key: value
            for key, value in fields.items()
            if value is not None
        })
