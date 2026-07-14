
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/bugcrowd/jobs?content=true"
EXTRA_HOSTS = []


def _clean(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?i)<\s*(?:br|hr)\s*/?\s*>|</?\s*(?:address|article|aside|blockquote|"
        r"div|dl|dt|dd|figcaption|figure|footer|h[1-6]|header|li|main|nav|ol|"
        r"p|pre|section|table|tr|ul)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    lines = []
    for line in text.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))
    for item in payload.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        departments = [
            entry.get("name")
            for entry in item.get("departments") or []
            if entry.get("name")
        ]
        offices = [
            entry.get("name")
            for entry in item.get("offices") or []
            if entry.get("name")
        ]
        kwargs = {
            "url": item.get("absolute_url"),
            "title": item.get("title"),
            "company": "Bugcrowd",
            "location": location,
            "description": _clean(item.get("content")),
            "source": SITE,
            "external_id": str(item.get("id")),
        }
        if offices:
            kwargs["locations"] = offices
        if departments:
            kwargs["department"] = departments[0]
        if item.get("first_published"):
            kwargs["posted_at"] = item["first_published"]
        yield kwargs
