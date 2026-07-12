import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/bitso/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    decoded = html.unescape(value or "")
    decoded = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/h[1-6]|/tr|/table|/ul|/ol)\s*/?\s*>",
        "\n",
        decoded,
    )
    decoded = re.sub(r"<[^>]+>", "", decoded)
    decoded = html.unescape(decoded)

    lines = []
    for line in decoded.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def crawl(ctx):
    response = ctx.fetch(SITE + "&pay_transparency=true")
    payload = json.loads(response)

    for posting in payload.get("jobs", []):
        location = (posting.get("location") or {}).get("name")
        record = {
            "url": posting.get("absolute_url"),
            "title": posting.get("title"),
            "company": "Bitso",
            "location": location,
            "description": _plain_text(posting.get("content")),
            "source": SITE,
            "external_id": (
                str(posting["id"]) if posting.get("id") is not None else None
            ),
            "apply_url": posting.get("absolute_url"),
            "posted_at": posting.get("first_published"),
            "expires_at": posting.get("application_deadline"),
        }

        offices = [
            office.get("name")
            for office in posting.get("offices", [])
            if office.get("name")
        ]
        if offices:
            record["locations"] = offices

        departments = [
            department.get("name")
            for department in posting.get("departments", [])
            if department.get("name")
        ]
        if departments:
            record["department"] = ", ".join(departments)

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
                record["salary_min"] = min(minimums)
            if maximums:
                record["salary_max"] = max(maximums)
            record["salary_currency"] = next(iter(currencies))

        yield {
            key: value
            for key, value in record.items()
            if value is not None
        }
