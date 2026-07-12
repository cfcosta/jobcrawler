
import json
import re
from html import unescape


SITE = "https://boards-api.greenhouse.io/v1/boards/harnessinc/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    value = unescape(value)
    value = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/ul|/ol|/h[1-6]|/tr|/table)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", " ", value)
    value = unescape(value)

    lines = []
    for line in value.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def crawl(ctx):
    response = ctx.fetch(SITE + "&pay_transparency=true")
    payload = json.loads(response)

    for item in payload.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        description = _plain_text(item.get("content") or "")
        title = (item.get("title") or "").strip()
        posting_url = item.get("absolute_url")

        if not title or not location or not description or not posting_url:
            continue

        fields = {
            "url": posting_url,
            "apply_url": posting_url,
            "title": title,
            "company": "Harness",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(item["id"]),
        }

        offices = [
            office["name"]
            for office in item.get("offices", [])
            if office.get("name")
        ]
        if offices:
            fields["locations"] = offices

        departments = [
            department["name"]
            for department in item.get("departments", [])
            if department.get("name")
        ]
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
