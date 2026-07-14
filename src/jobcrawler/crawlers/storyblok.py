
import json
import re
from html import unescape

SITE = "https://boards-api.greenhouse.io/v1/boards/storyblok/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    text = unescape(value or "")
    text = re.sub(
        r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)\s*>",
        "",
        text,
    )
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)<li\b[^>]*>", "\n- ", text)
    text = re.sub(
        r"(?i)</(?:p|div|li|ul|ol|h[1-6]|blockquote|section)\s*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<!--.*?-->|<[^>]+>", "", text)
    text = unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _compensation_fields(ranges):
    valid = [
        item
        for item in (ranges or [])
        if item.get("min_cents") is not None
        or item.get("max_cents") is not None
    ]
    currencies = {
        item.get("currency_type")
        for item in valid
        if item.get("currency_type")
    }
    if not valid or len(currencies) != 1:
        return {}

    fields = {"salary_currency": next(iter(currencies))}
    minimums = [
        item["min_cents"] / 100
        for item in valid
        if item.get("min_cents") is not None
    ]
    maximums = [
        item["max_cents"] / 100
        for item in valid
        if item.get("max_cents") is not None
    ]
    if minimums:
        fields["salary_min"] = min(minimums)
    if maximums:
        fields["salary_max"] = max(maximums)
    return fields


def crawl(ctx):
    raw = ctx.fetch(SITE + "&pay_transparency=true")
    payload = json.loads(raw)

    for item in payload.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        fields = {
            "url": item.get("absolute_url"),
            "title": item.get("title"),
            "company": "Storyblok",
            "location": location,
            "description": _plain_text(item.get("content")),
            "external_id": str(item["id"]),
            "source": "greenhouse",
        }

        offices = [
            office.get("name")
            for office in item.get("offices", [])
            if office.get("name")
        ]
        if offices:
            fields["locations"] = offices

        departments = [
            department.get("name")
            for department in item.get("departments", [])
            if department.get("name")
        ]
        if departments:
            fields["department"] = ", ".join(departments)

        if item.get("first_published"):
            fields["posted_at"] = item["first_published"]
        if item.get("application_deadline"):
            fields["expires_at"] = item["application_deadline"]

        fields.update(_compensation_fields(item.get("pay_input_ranges")))
        yield fields
