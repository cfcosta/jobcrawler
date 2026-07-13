
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/commercetools/jobs?content=true"
EXTRA_HOSTS = []

API_URL = SITE + "&pay_transparency=true"

_BLOCK_TAG_RE = re.compile(
    r"</?(?:address|article|aside|blockquote|br|div|figcaption|figure|footer|"
    r"h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|tbody|td|tfoot|"
    r"th|thead|tr|ul)\b[^>]*>",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(
    r"<(?:script|style)\b[^>]*>.*?</(?:script|style)\s*>",
    re.IGNORECASE | re.DOTALL,
)


def _clean_html(value):
    value = html.unescape(value or "")
    value = _SCRIPT_STYLE_RE.sub("\n", value)
    value = _BLOCK_TAG_RE.sub("\n", value)
    value = _TAG_RE.sub(" ", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _compensation_fields(ranges):
    valid = [
        item for item in (ranges or [])
        if item.get("min_cents") is not None
        and item.get("max_cents") is not None
        and item.get("currency_type")
    ]
    currencies = {item["currency_type"] for item in valid}
    if not valid or len(currencies) != 1:
        return {}

    return {
        "salary_min": min(item["min_cents"] for item in valid) / 100,
        "salary_max": max(item["max_cents"] for item in valid) / 100,
        "salary_currency": next(iter(currencies)),
    }


def crawl(ctx):
    payload = json.loads(ctx.fetch(API_URL))

    for item in payload.get("jobs", []):
        description = _clean_html(item.get("content"))

        # Exclude the board's stale internal placeholder posting.
        if not description or description.casefold() == "test":
            continue

        location_data = item.get("location") or {}
        location = location_data.get("name")
        if not location:
            continue

        record = {
            "url": item["absolute_url"],
            "title": item["title"],
            "company": "commercetools",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(item["id"]),
        }

        offices = [
            office["name"] for office in (item.get("offices") or [])
            if office.get("name")
        ]
        if offices:
            record["locations"] = offices

        departments = [
            department["name"] for department in (item.get("departments") or [])
            if department.get("name")
        ]
        if departments:
            record["department"] = ", ".join(departments)

        if item.get("first_published"):
            record["posted_at"] = item["first_published"]
        if item.get("application_deadline"):
            record["expires_at"] = item["application_deadline"]

        record.update(_compensation_fields(item.get("pay_input_ranges")))
        yield record
