import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/mariadbplc/jobs?content=true"
EXTRA_HOSTS = []


def clean_html(value):
    if not value:
        return None
    # Greenhouse entity-escapes the full HTML fragment.
    value = html.unescape(value)
    value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    value = re.sub(
        r"(?i)<br\s*/?>|</p\s*>|</li\s*>|</h[1-6]\s*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    lines = [" ".join(line.split()) for line in value.splitlines()]
    text = "\n".join(line for line in lines if line)
    return text or None


def crawl(ctx):
    data = ctx.fetch(SITE + "&pay_transparency=true")
    if isinstance(data, str):
        data = json.loads(data)

    for item in data.get("jobs", []):
        location_data = item.get("location") or {}
        if isinstance(location_data, dict):
            location = location_data.get("name")
        else:
            location = location_data

        locations = [
            office["name"]
            for office in (item.get("offices") or [])
            if isinstance(office, dict) and office.get("name")
        ]
        departments = [
            department["name"]
            for department in (item.get("departments") or [])
            if isinstance(department, dict) and department.get("name")
        ]

        fields = {
            "source": SITE,
            "url": item.get("absolute_url"),
            "apply_url": item.get("absolute_url"),
            "title": item.get("title"),
            "company": "MariaDB",
            "location": location,
            "description": clean_html(item.get("content")),
            "external_id": str(item["id"]) if item.get("id") is not None else None,
            "posted_at": item.get("first_published"),
            "expires_at": item.get("application_deadline"),
            "department": ", ".join(departments) if departments else None,
            "locations": locations or None,
        }

        ranges = item.get("pay_input_ranges") or []
        currencies = {
            pay_range.get("currency_type")
            for pay_range in ranges
            if pay_range.get("currency_type")
        }
        if ranges and len(currencies) == 1:
            minimums = [
                pay_range["min_cents"]
                for pay_range in ranges
                if pay_range.get("min_cents") is not None
            ]
            maximums = [
                pay_range["max_cents"]
                for pay_range in ranges
                if pay_range.get("max_cents") is not None
            ]
            if minimums:
                fields["salary_min"] = min(minimums) / 100
            if maximums:
                fields["salary_max"] = max(maximums) / 100
            fields["salary_currency"] = next(iter(currencies))

        yield {
            key: value
            for key, value in fields.items()
            if value is not None
        }
