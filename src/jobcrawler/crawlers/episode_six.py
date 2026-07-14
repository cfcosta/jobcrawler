
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/episodesix/jobs?content=true"
EXTRA_HOSTS = []

_BLOCK_TAGS = re.compile(
    r"</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|fieldset|"
    r"figcaption|figure|footer|form|h[1-6]|header|hr|li|main|nav|ol|p|"
    r"pre|section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
    re.IGNORECASE,
)
_ANY_TAG = re.compile(r"<[^>]+>")


def _description(value):
    value = html.unescape(value)
    value = _BLOCK_TAGS.sub("\n", value)
    value = _ANY_TAG.sub("", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))

    for posting in payload.get("jobs", []):
        location = (posting.get("location") or {}).get("name")
        departments = [
            item["name"]
            for item in posting.get("departments") or []
            if item.get("name")
        ]
        locations = [
            item["name"]
            for item in posting.get("offices") or []
            if item.get("name")
        ]

        fields = {
            "url": posting.get("absolute_url"),
            "source": SITE,
            "title": posting.get("title"),
            "company": "Episode Six",
            "location": location,
            "description": _description(posting.get("content") or ""),
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
