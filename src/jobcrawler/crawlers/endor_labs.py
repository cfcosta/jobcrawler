
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/endorlabs/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    # Decode Greenhouse's entity-escaped HTML before removing markup.
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)<\s*(br|hr)\s*/?\s*>|<\s*/?\s*(p|div|li|h[1-6]|ul|ol|"
        r"section|article|blockquote|pre|table|tr)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1\s*>", " ", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"[ \t]{2,}", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def crawl(ctx):
    response = ctx.fetch(SITE + "&pay_transparency=true")
    payload = response.json() if hasattr(response, "json") else json.loads(response)

    for item in payload.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        offices = [
            office.get("name")
            for office in item.get("offices", [])
            if office.get("name")
        ]
        departments = [
            department.get("name")
            for department in item.get("departments", [])
            if department.get("name")
        ]

        fields = {
            "url": item.get("absolute_url"),
            "source": SITE,
            "title": item.get("title"),
            "company": "Endor Labs",
            "location": location,
            "description": _plain_text(item.get("content")),
            "external_id": str(item.get("id")) if item.get("id") is not None else None,
            "posted_at": item.get("first_published"),
            "expires_at": item.get("application_deadline"),
            "department": ", ".join(departments) if departments else None,
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
                pay_range.get("min_cents") / 100
                for pay_range in ranges
                if pay_range.get("min_cents") is not None
            ]
            maximums = [
                pay_range.get("max_cents") / 100
                for pay_range in ranges
                if pay_range.get("max_cents") is not None
            ]
            fields["salary_min"] = min(minimums) if minimums else None
            fields["salary_max"] = max(maximums) if maximums else None
            fields["salary_currency"] = next(iter(currencies))

        yield {
            key: value for key, value in fields.items()
            if value is not None
        }
