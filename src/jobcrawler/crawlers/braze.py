import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/braze/jobs?content=true"
EXTRA_HOSTS = []

_BLOCK_TAGS = re.compile(
    r"</?(?:address|article|aside|blockquote|br|dd|div|dl|dt|figcaption|figure|"
    r"footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|tbody|td|"
    r"tfoot|th|thead|tr|ul)\b[^>]*>",
    re.IGNORECASE,
)
_TAGS = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"[ \t\f\v]+")


def _plain_text(value):
    text = html.unescape(value)
    text = _BLOCK_TAGS.sub("\n", text)
    text = _TAGS.sub("", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        cleaned = _WHITESPACE.sub(" ", line).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))

    for item in payload["jobs"]:
        offices = [
            office["name"]
            for office in item.get("offices", [])
            if office.get("name")
        ]
        departments = [
            department["name"]
            for department in item.get("departments", [])
            if department.get("name")
        ]

        fields = {
            "url": item["absolute_url"],
            "source": SITE,
            "title": item["title"],
            "company": "Braze",
            "location": item["location"]["name"],
            "description": _plain_text(item["content"]),
            "external_id": str(item["id"]),
            "apply_url": item["absolute_url"],
        }
        if offices:
            fields["locations"] = offices
        if departments:
            fields["department"] = ", ".join(departments)
        if item.get("first_published"):
            fields["posted_at"] = item["first_published"]

        yield fields
