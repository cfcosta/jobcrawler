import json
import re
from html import unescape


SITE = "https://boards-api.greenhouse.io/v1/boards/vectranetworks/jobs?content=true"
EXTRA_HOSTS = []

_BLOCK_TAGS = re.compile(
    r"</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|figcaption|figure|"
    r"footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|tbody|td|"
    r"tfoot|th|thead|tr|ul)\b[^>]*>",
    re.IGNORECASE,
)
_ALL_TAGS = re.compile(r"<[^>]+>")


def _plain_text(value):
    revealed = unescape(value or "")
    with_lines = _BLOCK_TAGS.sub("\n", revealed)
    without_tags = _ALL_TAGS.sub("", with_lines)
    decoded = unescape(without_tags).replace("\r", "\n")
    lines = []
    for line in decoded.split("\n"):
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _salary_fields(ranges):
    ranges = ranges or []
    currencies = {item.get("currency_type") for item in ranges}
    if not ranges or None in currencies or len(currencies) != 1:
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
    separator = "&" if "?" in SITE else "?"
    response = ctx.fetch(SITE + separator + "pay_transparency=true")
    data = json.loads(response)

    for item in data.get("jobs") or []:
        title = (item.get("title") or "").strip()
        location = ((item.get("location") or {}).get("name") or "").strip()
        description = _plain_text(item.get("content"))
        url = item.get("absolute_url")

        locations = [
            office["name"]
            for office in (item.get("offices") or [])
            if isinstance(office.get("name"), str) and office["name"].strip()
        ]
        departments = [
            department["name"].strip()
            for department in (item.get("departments") or [])
            if isinstance(department.get("name"), str)
            and department["name"].strip()
        ]

        fields = {
            "url": url,
            "source": SITE,
            "title": title,
            "company": item.get("company_name") or "Vectra",
            "location": location,
            "description": description,
            "external_id": str(item["id"]),
            "apply_url": url,
        }
        if locations:
            fields["locations"] = locations
        if departments:
            fields["department"] = ", ".join(departments)
        if item.get("first_published"):
            fields["posted_at"] = item["first_published"]
        if item.get("application_deadline"):
            fields["expires_at"] = item["application_deadline"]
        fields.update(_salary_fields(item.get("pay_input_ranges")))

        yield fields
