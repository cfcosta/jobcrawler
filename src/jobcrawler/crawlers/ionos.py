import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/ionos/jobs?content=true"
EXTRA_HOSTS = []


def _html_to_text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?is)<(script|style)\b[^>]*>.*?</\1\s*>",
        "",
        value,
    )
    value = re.sub(r"(?i)<li\b[^>]*>", "\n- ", value)
    value = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|div|footer|h[1-6]|"
        r"header|hr|main|ol|p|pre|section|table|tbody|td|th|tr|ul)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?i)</li\s*>", "\n", value)
    value = re.sub(r"(?s)<[^>]+>", "", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _metadata_value(item, name):
    for field in item.get("metadata") or []:
        if field.get("name") == name:
            value = field.get("value")
            if isinstance(value, list):
                joined = ", ".join(str(part) for part in value if part)
                return joined or None
            return value
    return None


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))

    for item in payload.get("jobs") or []:
        title = (item.get("title") or "").strip()
        location = (item.get("location") or {}).get("name")
        description = _html_to_text(item.get("content"))
        url = item.get("absolute_url")

        if not title or not location or not description or not url:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "IONOS",
            "location": location,
            "description": description,
            "external_id": str(item["id"]),
            "source": SITE,
        }

        offices = [
            office["name"]
            for office in item.get("offices") or []
            if office.get("name")
        ]
        if offices:
            fields["locations"] = offices

        departments = [
            department["name"]
            for department in item.get("departments") or []
            if department.get("name")
        ]
        if departments:
            fields["department"] = ", ".join(departments)

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
