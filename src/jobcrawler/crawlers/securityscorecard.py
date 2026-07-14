import html
import json
import re


SITE = 'https://boards-api.greenhouse.io/v1/boards/securityscorecard/jobs?content=true'
EXTRA_HOSTS = []


_BLOCK_TAGS = re.compile(
    r"</?(?:address|article|aside|blockquote|br|div|footer|h[1-6]|header|hr|"
    r"li|main|ol|p|pre|section|table|tr|ul)\\b[^>]*>",
    re.IGNORECASE,
)
_ALL_TAGS = re.compile(r"<[^>]+>")
_BLANK_LINES = re.compile(r"\\n[ \\t]*\\n+")


def _plain_text(value):
    text = html.unescape(value or "")
    text = _BLOCK_TAGS.sub("\\n", text)
    text = _ALL_TAGS.sub("", text)
    text = html.unescape(text)
    lines = []
    for line in _BLANK_LINES.sub("\\n", text).splitlines():
        clean = " ".join(line.split())
        if clean:
            lines.append(clean)
    return "\\n".join(lines)


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for record in payload.get("jobs", []):
        title = record.get("title")
        url = record.get("absolute_url")
        description = _plain_text(record.get("content"))
        location = (record.get("location") or {}).get("name")
        company = record.get("company_name") or "SecurityScorecard"

        if not title or not url or not description or not location:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "source": SITE,
        }

        if record.get("id") is not None:
            fields["external_id"] = str(record["id"])

        if record.get("first_published"):
            fields["posted_at"] = record["first_published"]

        office_names = [
            office.get("name")
            for office in record.get("offices", [])
            if office.get("name")
        ]
        if office_names:
            fields["locations"] = office_names

        department_names = [
            department.get("name")
            for department in record.get("departments", [])
            if department.get("name")
        ]
        if department_names:
            fields["department"] = ", ".join(department_names)

        if record.get("application_deadline"):
            fields["expires_at"] = record["application_deadline"]

        yield fields
