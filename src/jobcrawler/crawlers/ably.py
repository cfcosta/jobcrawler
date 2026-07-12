
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/ably30/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    decoded = html.unescape(value or "")
    decoded = re.sub(
        r"(?i)<\s*(?:br|p|div|li|h[1-6]|tr|section|article|blockquote|pre|ul|ol)\b[^>]*>",
        "\n",
        decoded,
    )
    decoded = re.sub(
        r"(?i)</\s*(?:p|div|li|h[1-6]|tr|section|article|blockquote|pre|ul|ol)\s*>",
        "\n",
        decoded,
    )
    decoded = re.sub(r"<[^>]+>", "", decoded)
    decoded = html.unescape(decoded)

    lines = []
    for line in decoded.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def crawl(ctx):
    response = ctx.fetch(SITE + "&pay_transparency=true")
    payload = json.loads(response)

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

        fields = {
            "url": posting.get("absolute_url"),
            "source": SITE,
            "title": posting.get("title"),
            "company": "Ably",
            "location": location,
            "description": _plain_text(posting.get("content")),
            "external_id": str(posting["id"]) if posting.get("id") is not None else None,
            "posted_at": posting.get("first_published"),
            "department": ", ".join(departments) if departments else None,
            "locations": offices or None,
        }
        yield {key: value for key, value in fields.items() if value is not None}
