import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/patronusaiinc/jobs?content=true"
EXTRA_HOSTS = []


def _description(value):
    value = html.unescape(value or "")
    value = re.sub(r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)>", " ", value)
    value = re.sub(
        r"(?i)<(?:br|/p|/div|/li|/h[1-6]|/tr|/section|/article|/ul|/ol)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        cleaned = re.sub(r"\s+", " ", line).strip()
        if cleaned and (not lines or cleaned != lines[-1]):
            lines.append(cleaned)
    return "\n".join(lines)


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for posting in payload.get("jobs", []):
        location_data = posting.get("location") or {}
        offices = [
            office.get("name")
            for office in posting.get("offices") or []
            if office.get("name")
        ]
        departments = [
            department.get("name")
            for department in posting.get("departments") or []
            if department.get("name")
        ]

        fields = {
            "url": posting.get("absolute_url"),
            "title": posting.get("title"),
            "company": "Patronus AI, Inc.",
            "location": location_data.get("name"),
            "description": _description(posting.get("content")),
            "source": "greenhouse",
            "external_id": str(posting["id"]) if posting.get("id") is not None else None,
            "posted_at": posting.get("first_published"),
            "expires_at": posting.get("application_deadline"),
            "locations": offices or None,
            "department": ", ".join(departments) if departments else None,
        }
        yield {key: value for key, value in fields.items() if value is not None}
