
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/encore/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    value = html.unescape(value)
    value = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/ul|/ol|/h[1-6]|/tr)\s*/?\s*>",
        "\n",
        value,
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
    payload = json.loads(ctx.fetch(SITE))

    for posting in payload.get("jobs", []):
        location = (posting.get("location") or {}).get("name")
        departments = [
            item["name"]
            for item in posting.get("departments", [])
            if item.get("name")
        ]
        offices = [
            item["name"]
            for item in posting.get("offices", [])
            if item.get("name")
        ]

        yield {
            "url": posting["absolute_url"],
            "title": posting["title"],
            "company": "Encore",
            "location": location,
            "locations": offices or None,
            "description": _plain_text(posting["content"]),
            "department": ", ".join(departments) or None,
            "external_id": str(posting["id"]),
            "source": SITE,
        }
