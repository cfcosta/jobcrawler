import json
import re
from html import unescape

SITE = "https://boards-api.greenhouse.io/v1/boards/platformsh/jobs?content=true"
EXTRA_HOSTS = []


_BLOCK_TAGS = re.compile(
    r"</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|figcaption|figure|"
    r"footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|tbody|td|"
    r"tfoot|th|thead|tr|ul)\b[^>]*>",
    re.IGNORECASE,
)
_TAGS = re.compile(r"<[^>]+>")


def _plain_text(value):
    text = unescape(value or "")
    text = _BLOCK_TAGS.sub("\n", text)
    text = _TAGS.sub("", text)
    text = unescape(text)

    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))

    for item in payload.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        offices = [
            office["name"]
            for office in item.get("offices") or []
            if office.get("name")
        ]
        departments = [
            department["name"]
            for department in item.get("departments") or []
            if department.get("name")
        ]

        yield dict(
            source=SITE,
            url=item["absolute_url"],
            title=item["title"],
            company=item.get("company_name") or "Platform.sh",
            location=location,
            locations=offices or None,
            description=_plain_text(item.get("content")),
            department=", ".join(departments) if departments else None,
            posted_at=item.get("first_published"),
            expires_at=item.get("application_deadline"),
            external_id=str(item["id"]),
        )
