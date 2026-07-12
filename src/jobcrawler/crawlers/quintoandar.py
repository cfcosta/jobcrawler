
import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/quintoandar/jobs?content=true"
EXTRA_HOSTS = []

_TAG_RE = re.compile(r"<[^>]+>")
_BREAK_RE = re.compile(r"(?i)<(?:br\s*/?|/p|/li|/div|/h[1-6])\s*>")
_SPACE_RE = re.compile(r"[ \t\f\v]+")
_NEWLINES_RE = re.compile(r"\n{3,}")


def _text(value):
    if not value:
        return None
    value = html.unescape(value)
    value = _BREAK_RE.sub("\n", value)
    value = _TAG_RE.sub("", value)
    value = html.unescape(value).replace("\r", "")
    value = "\n".join(_SPACE_RE.sub(" ", line).strip() for line in value.split("\n"))
    value = _NEWLINES_RE.sub("\n\n", value).strip()
    return value or None


def crawl(ctx):
    response = ctx.fetch(SITE + "&pay_transparency=true")
    data = json.loads(response)

    for posting in data.get("jobs", []):
        location = (posting.get("location") or {}).get("name")
        offices = posting.get("offices") or []
        locations = [
            office["name"]
            for office in offices
            if office.get("name")
        ]
        departments = [
            department["name"]
            for department in (posting.get("departments") or [])
            if department.get("name")
        ]

        kwargs = {
            "url": posting.get("absolute_url"),
            "source": SITE,
            "title": posting.get("title"),
            "company": posting.get("company_name") or "Grupo QuintoAndar",
            "location": location,
            "description": _text(posting.get("content")),
            "external_id": str(posting["id"]) if posting.get("id") is not None else None,
            "apply_url": posting.get("absolute_url"),
            "posted_at": posting.get("first_published"),
            "expires_at": posting.get("application_deadline"),
            "department": ", ".join(departments) if departments else None,
        }
        if locations:
            kwargs["locations"] = locations

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
                kwargs["salary_min"] = min(minimums)
            if maximums:
                kwargs["salary_max"] = max(maximums)
            kwargs["salary_currency"] = next(iter(currencies))

        yield {key: value for key, value in kwargs.items() if value is not None}
