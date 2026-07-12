import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/forter/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/h[1-6]|/tr|/section|/article)\s*/?\s*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))

    for posting in payload.get("jobs", []):
        locations = [
            office["name"]
            for office in posting.get("offices") or []
            if office.get("name")
        ]
        departments = [
            department["name"]
            for department in posting.get("departments") or []
            if department.get("name")
        ]

        fields = {
            "url": posting.get("absolute_url"),
            "title": posting.get("title"),
            "company": posting.get("company_name") or "Forter",
            "location": (posting.get("location") or {}).get("name"),
            "description": _plain_text(posting.get("content")),
            "source": SITE,
            "external_id": (
                str(posting["id"])
                if posting.get("id") is not None
                else None
            ),
            "posted_at": posting.get("first_published"),
            "expires_at": posting.get("application_deadline"),
            "department": ", ".join(departments) if departments else None,
            "locations": locations or None,
        }

        ranges = posting.get("pay_input_ranges") or []
        currencies = {
            item.get("currency_type")
            for item in ranges
            if item.get("currency_type")
        }
        if ranges and len(currencies) == 1:
            minimums = [
                item["min_cents"] / 100
                for item in ranges
                if item.get("min_cents") is not None
            ]
            maximums = [
                item["max_cents"] / 100
                for item in ranges
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
