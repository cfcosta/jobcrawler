import json
import re
from html import unescape


SITE = "https://boards-api.greenhouse.io/v1/boards/recordedfuture/jobs?content=true"
EXTRA_HOSTS = []


def _description_text(value):
    text = unescape(value)
    text = re.sub(
        r"(?is)<\s*(?:br|hr)\s*/?\s*>|</?\s*(?:address|article|aside|"
        r"blockquote|dd|div|dl|dt|figcaption|figure|footer|h[1-6]|header|"
        r"li|main|nav|ol|p|pre|section|table|td|th|tr|ul)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)\s*>", "", text)
    text = re.sub(r"(?s)<[^>]*>", "", text)
    text = unescape(text)

    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t\f\v]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def crawl(ctx):
    response = ctx.fetch(SITE + "&pay_transparency=true")
    payload = json.loads(response)

    for posting in payload.get("jobs", []):
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
            "url": posting["absolute_url"],
            "source": SITE,
            "title": posting["title"],
            "company": "Recorded Future",
            "location": location,
            "description": _description_text(posting.get("content") or ""),
            "external_id": str(posting["id"]),
            "posted_at": posting.get("first_published"),
        }
        if offices:
            fields["locations"] = offices
        if departments:
            fields["department"] = departments[0]

        yield fields
