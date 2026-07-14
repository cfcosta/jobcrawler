import html
import json
import re


SITE = 'https://boards-api.greenhouse.io/v1/boards/dashlane/jobs?content=true'
EXTRA_HOSTS = []

_API_URL = SITE + "&pay_transparency=true"


def _plain_text(value):
    value = html.unescape(value)
    value = re.sub(
        r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>",
        "",
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    value = re.sub(
        r"</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|figcaption|"
        r"figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|"
        r"tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def crawl(ctx):
    payload = json.loads(ctx.fetch(_API_URL))

    for item in payload.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        departments = [
            department["name"]
            for department in item.get("departments", [])
            if department.get("name")
        ]
        locations = [
            office["name"]
            for office in item.get("offices", [])
            if office.get("name")
        ]

        yield {
            "url": item["absolute_url"],
            "source": SITE,
            "title": item["title"].strip(),
            "company": "Dashlane",
            "location": location,
            "locations": locations or None,
            "description": _plain_text(item["content"]),
            "department": ", ".join(departments) if departments else None,
            "posted_at": item.get("first_published"),
            "expires_at": item.get("application_deadline"),
            "external_id": str(item["id"]),
        }
