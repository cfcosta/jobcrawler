import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/arizeai/jobs?content=true"
EXTRA_HOSTS = []

_BLOCK_TAGS = re.compile(
    r"</?(?:address|article|aside|blockquote|br|div|footer|h[1-6]|header|hr|"
    r"li|main|ol|p|pre|section|table|td|th|tr|ul)\b[^>]*>",
    re.IGNORECASE,
)
_TAGS = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"[ \t\f\v]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def _description(value):
    text = html.unescape(value or "")
    text = _BLOCK_TAGS.sub("\n", text)
    text = _TAGS.sub("", text)
    text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_WHITESPACE.sub(" ", line).strip() for line in text.split("\n")]
    text = "\n".join(line for line in lines if line)
    return _BLANK_LINES.sub("\n\n", text).strip()


def crawl(ctx):
    response = ctx.fetch(SITE)
    payload = json.loads(response)

    for item in payload.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        departments = [
            department["name"]
            for department in item.get("departments", [])
            if department.get("name")
        ]
        locations = [
            office["name"]
            for office in item.get("offices", [])
            if office.get("name")
        ]

        fields = {
            "source": SITE,
            "url": item["absolute_url"],
            "title": item["title"],
            "company": item.get("company_name") or "Arize AI",
            "location": location,
            "description": _description(item.get("content")),
            "external_id": str(item["id"]),
            "posted_at": item.get("first_published"),
            "expires_at": item.get("application_deadline"),
        }
        if locations:
            fields["locations"] = locations
        if departments:
            fields["department"] = ", ".join(departments)

        yield {
            key: value
            for key, value in fields.items()
            if value is not None
        }
