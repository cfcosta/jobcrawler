
import json
import re
from html import unescape


SITE = "https://boards-api.greenhouse.io/v1/boards/nasuni/jobs?content=true"
EXTRA_HOSTS = []

_BLOCK_TAGS = re.compile(
    r"</?(?:address|article|aside|blockquote|br|div|footer|h[1-6]|header|hr|"
    r"li|main|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
    re.IGNORECASE,
)
_HIDDEN = re.compile(
    r"<(?:script|style)\b[^>]*>.*?</(?:script|style)\s*>",
    re.IGNORECASE | re.DOTALL,
)
_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[ \t\f\v]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def _description_text(value):
    text = unescape(value or "")
    text = _HIDDEN.sub("", text)
    text = _BLOCK_TAGS.sub("\n", text)
    text = _TAGS.sub("", text)
    text = unescape(text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [_SPACES.sub(" ", line).strip() for line in text.split("\n")]
    return _BLANK_LINES.sub("\n\n", "\n".join(line for line in lines if line))


def crawl(ctx):
    response = ctx.fetch(SITE + "&pay_transparency=true")
    payload = json.loads(response)

    for item in payload.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        locations = [
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
            "url": item["absolute_url"],
            "title": item["title"],
            "company": item.get("company_name") or "Nasuni",
            "location": location,
            "description": _description_text(item.get("content")),
            "source": SITE,
            "external_id": str(item["id"]),
        }

        if locations:
            fields["locations"] = locations
        if departments:
            fields["department"] = departments[0]
        if item.get("first_published"):
            fields["posted_at"] = item["first_published"]
        if item.get("application_deadline"):
            fields["expires_at"] = item["application_deadline"]

        ranges = item.get("pay_input_ranges") or []
        currencies = {
            value.get("currency_type")
            for value in ranges
            if value.get("currency_type")
        }
        if ranges and len(currencies) == 1:
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
            if minimums:
                fields["salary_min"] = min(minimums)
            if maximums:
                fields["salary_max"] = max(maximums)
            fields["salary_currency"] = next(iter(currencies))

        yield fields
