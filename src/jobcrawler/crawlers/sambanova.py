
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/sambanovasystems/jobs?content=true"
EXTRA_HOSTS = []
API_URL = SITE + "&pay_transparency=true"


def _html_to_text(value):
    # Greenhouse returns the full posting HTML with an additional entity-escaped layer.
    value = html.unescape(value or "")
    value = re.sub(
        r"<\s*(?:br|hr|p|div|li|h[1-6]|tr|td|th|section|article|ul|ol)\b[^>]*>",
        "\n",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"</\s*(?:p|div|li|h[1-6]|tr|td|th|section|article|ul|ol)\s*>",
        "\n",
        value,
        flags=re.I,
    )
    value = re.sub(r"<(?:script|style|noscript|template)\b[^>]*>.*?</(?:script|style|noscript|template)\s*>", "", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        line = re.sub(r"[ \t\f\v]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _metadata(job, name):
    for item in job.get("metadata") or []:
        value = item.get("value")
        if item.get("name") == name and value not in (None, ""):
            return value
    return None


def crawl(ctx):
    payload = json.loads(ctx.fetch(API_URL))

    for item in payload.get("jobs") or []:
        location = (item.get("location") or {}).get("name")
        offices = [
            office.get("name")
            for office in (item.get("offices") or [])
            if office.get("name")
        ]
        departments = [
            department.get("name")
            for department in (item.get("departments") or [])
            if department.get("name")
        ]

        fields = {
            "url": item.get("absolute_url"),
            "source": SITE,
            "apply_url": item.get("absolute_url"),
            "title": item.get("title"),
            "company": "SambaNova Systems",
            "location": location,
            "description": _html_to_text(item.get("content")),
            "external_id": str(item["id"]) if item.get("id") is not None else None,
            "employment_type": _metadata(item, "Employment Type"),
            "department": ", ".join(departments) if departments else None,
            "posted_at": item.get("first_published"),
            "expires_at": item.get("application_deadline"),
        }

        if offices:
            fields["locations"] = offices

        ranges = item.get("pay_input_ranges") or []
        currencies = {
            pay.get("currency_type")
            for pay in ranges
            if pay.get("currency_type")
        }
        if len(ranges) == 1 and len(currencies) == 1:
            pay = ranges[0]
            if pay.get("min_cents") is not None:
                fields["salary_min"] = pay["min_cents"] / 100
            if pay.get("max_cents") is not None:
                fields["salary_max"] = pay["max_cents"] / 100
            fields["salary_currency"] = pay.get("currency_type")

        yield {
            key: value
            for key, value in fields.items()
            if value not in (None, "")
        }
