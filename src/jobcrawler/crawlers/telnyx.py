import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/telnyx54/jobs?content=true"
EXTRA_HOSTS = []


def _description(value):
    markup = html.unescape(value or "")
    markup = re.sub(
        r"(?i)<\s*(br|hr)\s*/?\s*>|<\s*/?\s*(p|div|section|article|"
        r"h[1-6]|ul|ol|table|tr|blockquote)\b[^>]*>",
        "\n",
        markup,
    )
    markup = re.sub(r"(?i)<\s*li\b[^>]*>", "\n- ", markup)
    markup = re.sub(r"(?i)<\s*/\s*li\s*>", "\n", markup)
    text = re.sub(r"<[^>]+>", "", markup)
    text = html.unescape(text).replace("\xa0", " ")

    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))

    for posting in payload.get("jobs", []):
        title = posting.get("title")
        location = (posting.get("location") or {}).get("name")
        description = _description(posting.get("content"))
        url = posting.get("absolute_url")
        if not title or not location or not description or not url:
            continue

        fields = {
            "url": url,
            "source": SITE,
            "title": title,
            "company": posting.get("company_name") or "Telnyx",
            "location": location,
            "description": description,
            "external_id": str(posting["id"]),
            "posted_at": posting.get("first_published"),
        }

        locations = [
            office["name"]
            for office in posting.get("offices") or []
            if isinstance(office, dict) and office.get("name")
        ]
        if locations:
            fields["locations"] = locations

        departments = [
            department["name"]
            for department in posting.get("departments") or []
            if isinstance(department, dict) and department.get("name")
        ]
        if departments:
            fields["department"] = "; ".join(departments)

        pay_ranges = posting.get("pay_input_ranges") or []
        currencies = {
            item.get("currency_type")
            for item in pay_ranges
            if item.get("currency_type")
        }
        if pay_ranges and len(currencies) == 1:
            minimums = [
                item["min_cents"] / 100
                for item in pay_ranges
                if item.get("min_cents") is not None
            ]
            maximums = [
                item["max_cents"] / 100
                for item in pay_ranges
                if item.get("max_cents") is not None
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
