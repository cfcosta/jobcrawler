import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/spycloud/jobs?content=true"
EXTRA_HOSTS = []
API_URL = SITE + "&pay_transparency=true"


def _plain_text(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?i)<(?:br|/p|/div|/li|/h[1-6]|/tr|/section|/article|/ul|/ol)\s*/?>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        clean = " ".join(line.split())
        if clean:
            lines.append(clean)
    return "\n".join(lines)


def crawl(ctx):
    payload = json.loads(ctx.fetch(API_URL))

    for item in payload.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        offices = [
            office["name"]
            for office in item.get("offices", [])
            if office.get("name")
        ]
        departments = [
            department["name"]
            for department in item.get("departments", [])
            if department.get("name")
        ]

        fields = {
            "url": item.get("absolute_url"),
            "title": item.get("title"),
            "company": "SpyCloud",
            "location": location,
            "description": _plain_text(item.get("content")),
            "source": SITE,
            "external_id": str(item["id"]) if item.get("id") is not None else None,
            "posted_at": item.get("first_published"),
            "expires_at": item.get("application_deadline"),
            "department": ", ".join(departments) if departments else None,
            "locations": offices or None,
        }

        yield {
            key: value
            for key, value in fields.items()
            if value is not None
        }
