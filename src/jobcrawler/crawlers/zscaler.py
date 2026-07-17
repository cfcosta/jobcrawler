
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/zscaler/jobs?content=true"
EXTRA_HOSTS = []

_BLOCK_TAGS = re.compile(
    r"</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|fieldset|"
    r"figcaption|figure|footer|form|h[1-6]|header|hr|li|main|nav|ol|p|"
    r"pre|section|table|tr|ul)\b[^>]*>",
    re.IGNORECASE,
)
_TAGS = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"[ \t\f\v]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def _text(value):
    value = html.unescape(value or "")
    value = _BLOCK_TAGS.sub("\n", value)
    value = _TAGS.sub("", value)
    value = html.unescape(value).replace("\r", "\n")
    lines = [_SPACE.sub(" ", line).strip() for line in value.split("\n")]
    return _BLANK_LINES.sub("\n\n", "\n".join(line for line in lines if line))


def crawl(ctx):
    response = ctx.fetch(SITE + "&pay_transparency=true")
    raw = response.text if hasattr(response, "text") else response
    data = json.loads(raw)

    for posting in data.get("jobs", []):
        location = (posting.get("location") or {}).get("name")
        if not location:
            continue

        metadata = {
            item.get("name"): item.get("value")
            for item in posting.get("metadata", [])
            if item.get("name")
        }
        departments = [
            item.get("name")
            for item in posting.get("departments", [])
            if item.get("name")
        ]
        offices = [
            item.get("name")
            for item in posting.get("offices", [])
            if item.get("name")
        ]

        job = {
            "url": posting.get("absolute_url"),
            "title": posting.get("title"),
            "company": posting.get("company_name") or "Zscaler",
            "location": location,
            "description": _text(posting.get("content")),
            "source": "greenhouse",
            "external_id": str(posting["id"]) if posting.get("id") is not None else None,
            "employment_type": metadata.get("Employment Type"),
            "department": ", ".join(departments) if departments else None,
            "posted_at": posting.get("first_published"),
            "expires_at": posting.get("application_deadline"),
        }
        if offices:
            job["locations"] = offices

        ranges = posting.get("pay_input_ranges") or []
        currencies = {
            item.get("currency_type")
            for item in ranges
            if item.get("currency_type")
        }
        if len(ranges) == 1 and len(currencies) == 1:
            pay = ranges[0]
            if pay.get("min_cents") is not None:
                job["salary_min"] = pay["min_cents"] / 100
            if pay.get("max_cents") is not None:
                job["salary_max"] = pay["max_cents"] / 100
            job["salary_currency"] = pay.get("currency_type")

        yield {key: value for key, value in job.items() if value not in (None, "")}
