
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/mixpanel/jobs?content=true"
EXTRA_HOSTS = []

API_URL = SITE + "&pay_transparency=true"


def _plain_text(value):
    value = html.unescape(value)
    value = re.sub(
        r"(?i)<\s*(?:br|hr)\s*/?\s*>|<\s*/\s*(?:address|article|aside|blockquote|"
        r"div|dl|dt|dd|figcaption|figure|footer|h[1-6]|header|li|main|nav|ol|p|"
        r"pre|section|table|tr|ul)\s*>",
        "\n",
        value,
    )
    value = re.sub(
        r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)\s*>",
        " ",
        value,
    )
    value = re.sub(r"(?s)<[^>]*>", " ", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def crawl(ctx):
    response = ctx.fetch(API_URL)
    if response is None:
        return

    raw = response if isinstance(response, str) else response.text
    payload = json.loads(raw)
    for item in payload.get("jobs", []):
        title = item.get("title")
        url = item.get("absolute_url")
        location = (item.get("location") or {}).get("name")
        content = item.get("content")
        if not title or not url or not location or not content:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": item.get("company_name") or "Mixpanel",
            "location": location,
            "description": _plain_text(content),
            "source": SITE,
            "external_id": str(item["id"]),
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
            fields["department"] = departments[0]

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
        if len(ranges) == 1 and len(currencies) == 1:
            pay_range = ranges[0]
            if pay_range.get("min_cents") is not None:
                fields["salary_min"] = pay_range["min_cents"] / 100
            if pay_range.get("max_cents") is not None:
                fields["salary_max"] = pay_range["max_cents"] / 100
            fields["salary_currency"] = next(iter(currencies))

        yield fields
