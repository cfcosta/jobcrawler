
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/clara/jobs?content=true"
EXTRA_HOSTS = []


def _description(value):
    decoded = value or ""
    for _ in range(4):
        unescaped = html.unescape(decoded)
        if unescaped == decoded:
            break
        decoded = unescaped

    decoded = re.sub(
        r"(?is)<(script|style)\b[^>]*>.*?</\1\s*>",
        " ",
        decoded,
    )
    decoded = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/h[1-6]|/tr|/section|/article|/ul|/ol)\s*/?\s*>",
        "\n",
        decoded,
    )
    decoded = re.sub(r"(?s)<[^>]+>", " ", decoded)
    decoded = html.unescape(decoded)

    lines = []
    for line in decoded.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def crawl(ctx):
    response = ctx.fetch(SITE)
    raw = response.text if hasattr(response, "text") else response
    data = json.loads(raw)

    for posting in data.get("jobs", []):
        location = (posting.get("location") or {}).get("name")
        offices = [
            office["name"]
            for office in posting.get("offices") or []
            if office.get("name")
        ]
        departments = [
            department["name"]
            for department in posting.get("departments") or []
            if department.get("name")
        ]

        fields = {
            "url": posting.get("absolute_url"),
            "source": SITE,
            "title": posting.get("title"),
            "company": (posting.get("company_name") or "Clara").strip(),
            "location": location,
            "description": _description(posting.get("content")),
            "external_id": str(posting["id"]) if posting.get("id") is not None else None,
            "department": ", ".join(departments) if departments else None,
            "posted_at": posting.get("first_published"),
            "expires_at": posting.get("application_deadline"),
            "apply_url": posting.get("absolute_url"),
            "locations": offices or None,
        }
        yield {key: value for key, value in fields.items() if value is not None}
