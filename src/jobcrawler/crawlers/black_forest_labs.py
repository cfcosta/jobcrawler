import html
import json
import re

SITE = 'https://boards-api.greenhouse.io/v1/boards/blackforestlabs/jobs?content=true'
EXTRA_HOSTS = []


def _description(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?i)<br\\s*/?>|</(?:p|div|li|h[1-6]|tr|section|article|ul|ol)>",
        "\\n",
        text,
    )
    text = re.sub(r"(?i)<li(?:\\s[^>]*)?>", "\\n- ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        normalized = " ".join(line.split())
        if normalized:
            lines.append(normalized)
    return "\\n".join(lines)


def crawl(ctx):
    response = ctx.fetch(SITE + "&pay_transparency=true")
    payload = json.loads(response)

    for posting in payload.get("jobs", []):
        location = (posting.get("location") or {}).get("name")
        departments = [
            item.get("name")
            for item in posting.get("departments") or []
            if item.get("name")
        ]
        locations = [
            item.get("name")
            for item in posting.get("offices") or []
            if item.get("name")
        ]

        data = {
            "url": posting.get("absolute_url"),
            "title": posting.get("title"),
            "company": "Black Forest Labs",
            "location": location,
            "description": _description(posting.get("content")),
            "external_id": str(posting["id"]) if posting.get("id") is not None else None,
            "apply_url": posting.get("absolute_url"),
            "posted_at": posting.get("first_published"),
            "expires_at": posting.get("application_deadline"),
            "department": ", ".join(departments) if departments else None,
            "locations": locations or None,
            "source": SITE,
        }
        data = {key: value for key, value in data.items() if value is not None}
        yield data
