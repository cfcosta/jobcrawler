import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/lokalise/jobs?content=true"
EXTRA_HOSTS = []
DISCOVERY_URL = SITE + "&pay_transparency=true"


def _plain_text(value):
    text = html.unescape(value or "")
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1\s*>", " ", text)
    text = re.sub(
        r"(?i)<(?:br|/p|/div|/li|/h[1-6]|/tr|/table|/section|/article)\s*/?>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _salary_fields(posting):
    ranges = posting.get("pay_input_ranges") or []
    currencies = {
        item.get("currency_type")
        for item in ranges
        if item.get("currency_type")
    }
    if not ranges or len(currencies) != 1:
        return {}

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
    result = {"salary_currency": next(iter(currencies))}
    if minimums:
        result["salary_min"] = min(minimums)
    if maximums:
        result["salary_max"] = max(maximums)

    periods = {
        item.get("pay_period")
        for item in ranges
        if item.get("pay_period")
    }
    if len(periods) == 1:
        result["salary_period"] = next(iter(periods))
    return result


def crawl(ctx):
    response = ctx.fetch(DISCOVERY_URL)
    data = json.loads(response)

    for posting in data.get("jobs", []):
        description = _plain_text(posting.get("content"))
        location = (posting.get("location") or {}).get("name")
        if not posting.get("title") or not posting.get("absolute_url"):
            continue
        if not description or not location or posting.get("id") is None:
            continue

        fields = {
            "url": posting["absolute_url"],
            "title": posting["title"],
            "company": posting.get("company_name") or "Lokalise",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(posting["id"]),
        }

        offices = [
            office["name"]
            for office in posting.get("offices") or []
            if office.get("name")
        ]
        if offices:
            fields["locations"] = offices

        departments = [
            department["name"]
            for department in posting.get("departments") or []
            if department.get("name")
        ]
        if departments:
            fields["department"] = ", ".join(departments)

        if posting.get("first_published"):
            fields["posted_at"] = posting["first_published"]
        if posting.get("application_deadline"):
            fields["expires_at"] = posting["application_deadline"]

        fields.update(_salary_fields(posting))
        yield fields
