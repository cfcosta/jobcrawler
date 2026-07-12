
import json
import re
from html import unescape



SITE = "https://boards-api.greenhouse.io/v1/boards/buildkite/jobs?content=true"
EXTRA_HOSTS = []


def _html_to_text(value):
    # Greenhouse returns the full description as entity-escaped HTML.
    text = unescape(value or "")
    text = re.sub(
        r"<(?:script|style|noscript|template)\b[^>]*>.*?</(?:script|style|noscript|template)\s*>",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<\s*br\s*/?\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<\s*li\b[^>]*>", "\n- ", text, flags=re.IGNORECASE)
    text = re.sub(
        r"</?\s*(?:address|article|aside|blockquote|div|dl|fieldset|figcaption|figure|footer|form|h[1-6]|header|hr|main|nav|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _department(item):
    names = [
        department.get("name")
        for department in (item.get("departments") or [])
        if department.get("name")
    ]
    return ", ".join(names) or None


def _locations(item):
    names = [
        office.get("name")
        for office in (item.get("offices") or [])
        if office.get("name")
    ]
    return names or None


def _salary_fields(item):
    ranges = item.get("pay_input_ranges") or []
    currencies = {
        pay_range.get("currency_type")
        for pay_range in ranges
        if pay_range.get("currency_type")
    }
    if not ranges or len(currencies) != 1:
        return {}

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

    result = {"salary_currency": next(iter(currencies))}
    if minimums:
        result["salary_min"] = min(minimums)
    if maximums:
        result["salary_max"] = max(maximums)
    return result


def crawl(ctx):
    response = ctx.fetch(
        "https://boards-api.greenhouse.io/v1/boards/buildkite/jobs"
        "?content=true&pay_transparency=true"
    )
    payload = json.loads(response)

    for item in payload.get("jobs", []):
        location_data = item.get("location") or {}
        fields = {
            "url": item.get("absolute_url"),
            "title": item.get("title"),
            "company": "Buildkite",
            "location": location_data.get("name"),
            "source": SITE,
            "description": _html_to_text(item.get("content")),
            "external_id": str(item.get("id")) if item.get("id") is not None else None,
            "department": _department(item),
            "posted_at": item.get("first_published"),
            "expires_at": item.get("application_deadline"),
            "locations": _locations(item),
        }
        fields.update(_salary_fields(item))
        yield {
            key: value
            for key, value in fields.items()
            if value is not None
        }
