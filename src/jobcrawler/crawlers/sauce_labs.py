
import html
import json
import re

SITE = 'https://boards-api.greenhouse.io/v1/boards/saucelabs/jobs?content=true'
EXTRA_HOSTS = []

_TAG_RE = re.compile(r"<[^>]+>")
_BREAK_RE = re.compile(r"(?i)<(?:br\s*/?|/p|/div|/li|/h[1-6])\s*>")
_SPACE_RE = re.compile(r"[ \t\f\v]+")
_BLANK_RE = re.compile(r"\n{3,}")


def _text(value):
    if not value:
        return None
    value = html.unescape(value)
    value = _BREAK_RE.sub("\n", value)
    value = _TAG_RE.sub("", value)
    value = html.unescape(value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = "\n".join(_SPACE_RE.sub(" ", line).strip() for line in value.split("\n"))
    value = _BLANK_RE.sub("\n\n", value).strip()
    return value or None


def crawl(ctx):
    response = ctx.fetch(SITE + "&pay_transparency=true")
    raw = response.text if hasattr(response, "text") else response
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    data = json.loads(raw)

    for posting in data.get("jobs", []):
        location = (posting.get("location") or {}).get("name")
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

        pay_ranges = posting.get("pay_input_ranges") or []
        currencies = {
            item.get("currency_type")
            for item in pay_ranges
            if item.get("currency_type")
        }
        salary_fields = {}
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
                salary_fields["salary_min"] = min(minimums)
            if maximums:
                salary_fields["salary_max"] = max(maximums)
            salary_fields["salary_currency"] = next(iter(currencies))

        fields = {
            "url": posting.get("absolute_url"),
            "source": SITE,
            "title": posting.get("title"),
            "company": posting.get("company_name") or "Sauce Labs",
            "location": location,
            "description": _text(posting.get("content")),
            "external_id": str(posting["id"]) if posting.get("id") is not None else None,
            "department": ", ".join(departments) if departments else None,
            "locations": locations or None,
            "posted_at": posting.get("first_published"),
            "expires_at": posting.get("application_deadline"),
        }
        fields.update(salary_fields)
        yield {key: value for key, value in fields.items() if value is not None}
