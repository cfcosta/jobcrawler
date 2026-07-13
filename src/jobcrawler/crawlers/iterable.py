import html
import json
import re


def Job(**fields):
    return fields


SITE = 'https://boards-api.greenhouse.io/v1/boards/iterable/jobs?content=true'
EXTRA_HOSTS = []


_BLOCK_TAGS = re.compile(
    r"</?(?:address|article|aside|blockquote|br|div|footer|h[1-6]|header|hr|"
    r"li|main|ol|p|pre|section|table|tbody|td|th|tr|ul)\b[^>]*>",
    re.IGNORECASE,
)
_ANY_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"[ \t\f\v]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def _plain_text(value):
    value = html.unescape(value or "")
    value = _BLOCK_TAGS.sub("\n", value)
    value = _ANY_TAG.sub("", value)
    value = html.unescape(value)
    lines = [_WHITESPACE.sub(" ", line).strip() for line in value.splitlines()]
    return _BLANK_LINES.sub("\n\n", "\n".join(
        line for line in lines if line
    )).strip()


def crawl(ctx):
    separator = "&" if "?" in SITE else "?"
    response = ctx.fetch(SITE + separator + "pay_transparency=true")
    payload = json.loads(response)

    for item in payload.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        locations = [
            office["name"]
            for office in item.get("offices") or []
            if isinstance(office, dict) and office.get("name")
        ]
        departments = [
            department["name"]
            for department in item.get("departments") or []
            if isinstance(department, dict) and department.get("name")
        ]

        fields = {
            "url": item["absolute_url"],
            "title": item["title"],
            "company": item.get("company_name") or "Iterable",
            "location": location,
            "description": _plain_text(item.get("content")),
            "external_id": str(item["id"]),
            "source": "Greenhouse",
        }

        if locations:
            fields["locations"] = locations
        if departments:
            fields["department"] = ", ".join(departments)
        if item.get("first_published"):
            fields["posted_at"] = item["first_published"]
        if item.get("application_deadline"):
            fields["expires_at"] = item["application_deadline"]

        ranges = item.get("pay_input_ranges") or []
        currencies = {
            pay_range.get("currency_type")
            for pay_range in ranges
            if isinstance(pay_range, dict) and pay_range.get("currency_type")
        }
        if ranges and len(currencies) == 1:
            minimums = [
                pay_range["min_cents"] / 100
                for pay_range in ranges
                if isinstance(pay_range, dict)
                and pay_range.get("min_cents") is not None
            ]
            maximums = [
                pay_range["max_cents"] / 100
                for pay_range in ranges
                if isinstance(pay_range, dict)
                and pay_range.get("max_cents") is not None
            ]
            if minimums:
                fields["salary_min"] = min(minimums)
            if maximums:
                fields["salary_max"] = max(maximums)
            fields["salary_currency"] = next(iter(currencies))

        yield Job(**fields)
