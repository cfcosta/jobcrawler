import json
import re
from html import unescape


SITE = 'https://boards-api.greenhouse.io/v1/boards/faire/jobs?content=true'
EXTRA_HOSTS = []


_BLOCK_TAG_RE = re.compile(
    r"</?(?:address|article|aside|blockquote|br|div|footer|h[1-6]|header|hr|"
    r"li|main|ol|p|pre|section|table|tr|ul)\b[^>]*>",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_LINE_SPACE_RE = re.compile(r"[ \t\f\v]+")
_BLANK_LINES_RE = re.compile(r"\n(?:[ \t]*\n)+")


def _description(content):
    text = unescape(content)
    text = _BLOCK_TAG_RE.sub("\\n", text)
    text = _TAG_RE.sub("", text)
    text = unescape(text).replace("\\r\\n", "\\n").replace("\\r", "\\n")
    text = _LINE_SPACE_RE.sub(" ", text)
    text = "\\n".join(line.strip() for line in text.split("\\n"))
    return _BLANK_LINES_RE.sub("\\n", text).strip()


def _metadata_value(job, name):
    for item in job.get("metadata") or []:
        if item.get("name") == name and item.get("value") is not None:
            return str(item["value"])
    return None


def crawl(ctx):
    url = SITE + ("&" if "?" in SITE else "?") + "pay_transparency=true"
    data = json.loads(ctx.fetch(url))

    for item in data.get("jobs", []):
        location = item.get("location") or {}
        office_names = [
            office["name"]
            for office in (item.get("offices") or [])
            if office.get("name")
        ]
        department_names = [
            department["name"]
            for department in (item.get("departments") or [])
            if department.get("name")
        ]

        fields = {
            "url": item["absolute_url"],
            "title": item["title"],
            "company": "Faire",
            "location": location["name"],
            "description": _description(item["content"]),
            "external_id": str(item["id"]),
            "source": SITE,
        }

        if office_names:
            fields["locations"] = office_names
        if department_names:
            fields["department"] = ", ".join(department_names)

        employment_type = _metadata_value(item, "Employment Type")
        if employment_type:
            fields["employment_type"] = employment_type
        if item.get("first_published"):
            fields["posted_at"] = item["first_published"]
        if item.get("application_deadline"):
            fields["expires_at"] = item["application_deadline"]

        ranges = item.get("pay_input_ranges") or []
        currencies = {
            pay_range.get("currency_type")
            for pay_range in ranges
            if pay_range.get("currency_type")
        }
        if ranges and len(currencies) == 1:
            minimums = [
                pay_range["min_cents"] / 100
                for pay_range in ranges
                if pay_range.get("min_cents") is not None
            ]
            maximums = [
                pay_range["max_cents"] / 100
                for pay_range in ranges
                if pay_range.get("max_cents") is not None
            ]
            if minimums:
                fields["salary_min"] = min(minimums)
            if maximums:
                fields["salary_max"] = max(maximums)
            fields["salary_currency"] = next(iter(currencies))

        yield fields
