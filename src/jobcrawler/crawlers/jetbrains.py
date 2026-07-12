
import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/jetbrains/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|"
        r"figcaption|figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|"
        r"pre|section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    lines = []
    for line in value.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for posting in payload.get("jobs", []):
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
            "url": posting.get("absolute_url"),
            "title": posting.get("title"),
            "company": "JetBrains",
            "source": SITE,
            "location": (posting.get("location") or {}).get("name"),
            "description": _plain_text(posting.get("content")),
            "external_id": str(posting["id"]) if posting.get("id") is not None else None,
            "posted_at": posting.get("first_published"),
            "expires_at": posting.get("application_deadline"),
            "department": departments[0] if departments else None,
            "locations": offices or None,
            "apply_url": posting.get("absolute_url"),
        }
        yield {
            key: value
            for key, value in fields.items()
            if value is not None
        }
