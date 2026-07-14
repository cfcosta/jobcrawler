import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/acryldata/jobs?content=true"
EXTRA_HOSTS = []

_BLOCK_TAGS = re.compile(
    r"</?(?:address|article|aside|blockquote|br|div|footer|h[1-6]|header|hr|li|"
    r"main|ol|p|pre|section|table|tr|ul)\b[^>]*>",
    re.IGNORECASE,
)
_ALL_TAGS = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"[ \t\f\v]+")
_BLANK_LINES = re.compile(r"\n(?:[ \t]*\n)+")


def _plain_text(value):
    # Greenhouse entity-escapes the complete HTML document.
    decoded = html.unescape(value)
    decoded = _BLOCK_TAGS.sub("\n", decoded)
    decoded = _ALL_TAGS.sub("", decoded)
    decoded = html.unescape(decoded).replace("\r\n", "\n").replace("\r", "\n")
    decoded = _WHITESPACE.sub(" ", decoded)
    decoded = "\n".join(line.strip() for line in decoded.splitlines())
    return _BLANK_LINES.sub("\n", decoded).strip()


def crawl(ctx):
    response = ctx.fetch(SITE + "&pay_transparency=true")
    data = json.loads(response)

    for posting in data.get("jobs", []):
        location = (posting.get("location") or {}).get("name")
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

        fields = {
            "url": posting["absolute_url"],
            "source": SITE,
            "title": posting["title"],
            "company": "DataHub",
            "location": location,
            "description": _plain_text(posting.get("content") or ""),
            "external_id": str(posting["id"]),
            "posted_at": posting.get("first_published"),
            "expires_at": posting.get("application_deadline"),
            "department": " / ".join(departments) if departments else None,
            "locations": offices or None,
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
