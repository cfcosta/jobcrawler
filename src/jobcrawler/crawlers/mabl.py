import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/mabl/jobs?content=true"
EXTRA_HOSTS = []


def _description(raw_html):
    # Greenhouse entity-escapes both markup and entities inside that markup.
    text = html.unescape(html.unescape(raw_html))
    text = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|dd|div|dl|dt|figcaption|figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]+>", "", text)
    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for item in data.get("jobs", []):
        location = item.get("location") or {}
        departments = [
            department["name"]
            for department in item.get("departments", [])
            if department.get("name")
        ]
        office_names = [
            office["name"]
            for office in item.get("offices", [])
            if office.get("name")
        ]

        fields = {
            "url": item["absolute_url"],
            "title": item["title"],
            "company": "mabl",
            "location": location.get("name"),
            "description": _description(item.get("content") or ""),
            "source": SITE,
            "external_id": str(item["id"]),
            "posted_at": item.get("first_published"),
            "expires_at": item.get("application_deadline"),
        }
        if departments:
            fields["department"] = ", ".join(departments)
        if office_names:
            fields["locations"] = office_names

        yield {
            key: value
            for key, value in fields.items()
            if value is not None
        }
