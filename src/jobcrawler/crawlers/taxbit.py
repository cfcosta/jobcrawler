import json
import re
from html import unescape

SITE = "https://boards-api.greenhouse.io/v1/boards/taxbit/jobs?content=true"
EXTRA_HOSTS = []


def _html_to_text(value):
    text = unescape(value or "")
    text = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/ul|/ol|/h[1-6]|/tr|/section|/article)\s*/?\s*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]*>", "", text)
    text = unescape(text).replace("\xa0", " ")
    lines = []
    for line in text.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def crawl(ctx):
    response = ctx.fetch(SITE + "&pay_transparency=true")
    body = response if isinstance(response, str) else response.text
    payload = json.loads(body)

    for posting in payload.get("jobs", []):
        title = posting.get("title")
        canonical_url = posting.get("absolute_url")
        location = (posting.get("location") or {}).get("name")
        description = _html_to_text(posting.get("content"))

        if not title or not canonical_url or not location or not description:
            continue

        fields = {
            "url": canonical_url,
            "title": title,
            "company": "Taxbit",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(posting["id"]),
            "apply_url": canonical_url,
        }

        locations = [
            office["name"]
            for office in posting.get("offices", [])
            if office.get("name")
        ]
        if locations:
            fields["locations"] = locations

        departments = [
            department["name"]
            for department in posting.get("departments", [])
            if department.get("name")
        ]
        if departments:
            fields["department"] = ", ".join(departments)

        if posting.get("first_published"):
            fields["posted_at"] = posting["first_published"]
        if posting.get("application_deadline"):
            fields["expires_at"] = posting["application_deadline"]

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

        yield fields
