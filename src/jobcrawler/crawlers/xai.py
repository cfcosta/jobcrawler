
import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/xai/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?is)<(script|style)\b[^>]*>.*?</\1\s*>",
        "",
        text,
    )
    text = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|"
        r"fieldset|figcaption|figure|footer|form|h[1-6]|header|hr|li|"
        r"main|nav|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)"
        r"\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def crawl(ctx):
    response = ctx.fetch(SITE + "&pay_transparency=true")
    payload = json.loads(response)

    for item in payload.get("jobs", []):
        description = _plain_text(item.get("content"))
        title = (item.get("title") or "").strip()
        location = (item.get("location") or {}).get("name")
        url = item.get("absolute_url")

        if not title or not location or not description or not url:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": item.get("company_name") or "xAI",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(item["id"]) if item.get("id") is not None else None,
            "posted_at": item.get("first_published"),
            "expires_at": item.get("application_deadline"),
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
            if value is not None
        }
