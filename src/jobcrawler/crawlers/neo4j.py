
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/neo4j/jobs?content=true"
EXTRA_HOSTS = []


def Job(**fields):
    return fields

_BLOCK_TAGS = re.compile(
    r"</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|figcaption|figure|"
    r"footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|tr|ul)\b[^>]*>",
    re.IGNORECASE,
)
_ALL_TAGS = re.compile(r"<[^>]+>")
_BLANK_LINES = re.compile(r"\n{3,}")


def _plain_text(value):
    decoded = html.unescape(value or "")
    with_breaks = _BLOCK_TAGS.sub("\n", decoded)
    stripped = _ALL_TAGS.sub("", with_breaks)
    decoded_text = html.unescape(stripped)

    lines = []
    for line in decoded_text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def _names(items):
    values = []
    for item in items or []:
        value = item.get("name") if isinstance(item, dict) else item
        if value:
            values.append(str(value))
    return values


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs", []):
        location = (posting.get("location") or {}).get("name")
        departments = _names(posting.get("departments"))
        offices = _names(posting.get("offices"))
        url = posting.get("absolute_url")

        fields = {
            "url": url,
            "source": SITE,
            "title": posting.get("title"),
            "company": posting.get("company_name") or "Neo4j",
            "location": location,
            "description": _plain_text(posting.get("content")),
            "external_id": str(posting["id"]) if posting.get("id") is not None else None,
            "apply_url": url,
            "posted_at": posting.get("first_published"),
            "expires_at": posting.get("application_deadline"),
            "department": ", ".join(departments) if departments else None,
            "locations": offices or None,
        }
        yield Job(**{key: value for key, value in fields.items() if value is not None})
