import html
import json
import re

from jobcrawler.runtime.contract import Job


SITE = "https://boards-api.greenhouse.io/v1/boards/alpaca/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?i)<br\s*/?>|</(?:p|div|li|h[1-6]|tr|section|article|ul|ol)\s*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    lines = []
    for line in text.splitlines():
        clean = " ".join(line.split())
        if clean:
            lines.append(clean)
    return "\n".join(lines)


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))

    for posting in payload.get("jobs", []):
        location_data = posting.get("location") or {}
        departments = [
            item.get("name")
            for item in posting.get("departments", [])
            if item.get("name")
        ]
        locations = [
            item.get("name")
            for item in posting.get("offices", [])
            if item.get("name")
        ]

        fields = {
            "url": posting["absolute_url"],
            "title": posting["title"],
            "company": posting.get("company_name") or "Alpaca",
            "location": location_data.get("name"),
            "locations": locations,
            "description": _plain_text(posting.get("content")),
            "source": "Greenhouse",
            "external_id": str(posting["id"]),
            "posted_at": posting.get("first_published"),
            "expires_at": posting.get("application_deadline"),
        }

        if departments:
            fields["department"] = ", ".join(departments)

        ranges = posting.get("pay_input_ranges") or []
        currencies = {
            item.get("currency_type")
            for item in ranges
            if item.get("currency_type")
        }
        if len(ranges) == 1 and len(currencies) == 1:
            pay_range = ranges[0]
            if pay_range.get("min_cents") is not None:
                fields["salary_min"] = pay_range["min_cents"] / 100
            if pay_range.get("max_cents") is not None:
                fields["salary_max"] = pay_range["max_cents"] / 100
            fields["salary_currency"] = next(iter(currencies))

        fields = {
            key: value
            for key, value in fields.items()
            if value is not None and value != "" and value != []
        }
        yield Job(**fields)
