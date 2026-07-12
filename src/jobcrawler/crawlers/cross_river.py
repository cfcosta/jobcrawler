import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/crossriverbank/jobs?content=true"
EXTRA_HOSTS = []


def clean_description(value):
    decoded = value or ""
    for _ in range(3):
        unescaped = html.unescape(decoded)
        if unescaped == decoded:
            break
        decoded = unescaped

    decoded = re.sub(
        r"(?i)</?(?:p|div|section|article|h[1-6]|ul|ol|li|tr|table|blockquote|pre|br|hr)\b[^>]*>",
        "\n",
        decoded,
    )
    decoded = re.sub(r"(?is)<script\b[^>]*>.*?</script>", " ", decoded)
    decoded = re.sub(r"(?is)<style\b[^>]*>.*?</style>", " ", decoded)
    decoded = re.sub(r"(?s)<[^>]+>", " ", decoded)
    decoded = html.unescape(decoded)

    lines = []
    for line in decoded.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def crawl(ctx):
    response = ctx.fetch(SITE + "&pay_transparency=true")
    payload = json.loads(response)

    for item in payload.get("jobs", []):
        departments = [
            department.get("name")
            for department in item.get("departments") or []
            if department.get("name")
        ]
        offices = [
            office.get("name")
            for office in item.get("offices") or []
            if office.get("name")
        ]

        fields = {
            "url": item.get("absolute_url"),
            "source": SITE,
            "title": item.get("title"),
            "company": "Cross River",
            "location": (item.get("location") or {}).get("name"),
            "description": clean_description(item.get("content")),
            "external_id": str(item["id"]) if item.get("id") is not None else None,
            "posted_at": item.get("first_published"),
            "department": departments[0] if len(departments) == 1 else None,
            "locations": offices or None,
        }

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

        yield {
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        }
