import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/vectara/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(escaped_html):
    value = html.unescape(escaped_html)
    value = re.sub(
        r"(?is)<\s*(?:br|/p|/div|/li|/ul|/ol|/h[1-6]|/tr|/table)\s*/?\s*>",
        "\n",
        value,
    )
    value = re.sub(r"(?is)<[^>]+>", "", value)
    value = html.unescape(value)
    lines = []
    for line in value.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for posting in data.get("jobs", []):
        location = (posting.get("location") or {}).get("name")
        departments = [
            item["name"] for item in posting.get("departments", [])
            if item.get("name")
        ]
        offices = [
            item["name"] for item in posting.get("offices", [])
            if item.get("name")
        ]
        fields = {
            "url": posting.get("absolute_url"),
            "title": posting.get("title"),
            "company": "Vectara",
            "location": location,
            "description": _plain_text(posting.get("content") or ""),
            "source": SITE,
            "external_id": str(posting["id"]) if posting.get("id") is not None else None,
            "posted_at": posting.get("first_published"),
            "expires_at": posting.get("application_deadline"),
            "department": ", ".join(departments) if departments else None,
            "locations": offices or None,
        }
        yield {key: value for key, value in fields.items() if value is not None}
