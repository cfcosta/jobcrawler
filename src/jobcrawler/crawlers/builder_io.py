
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/builder/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?is)<(script|style|noscript)\b[^>]*>.*?</\1\s*>",
        "",
        text,
    )
    text = re.sub(
        r"(?i)<(?:br|hr)\s*/?>|</?(?:address|article|aside|blockquote|div|dl|dt|dd|"
        r"figcaption|figure|footer|h[1-6]|header|li|main|ol|p|pre|section|table|"
        r"tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))
    seen = set()

    for item in payload.get("jobs", []):
        job_id = item.get("id")
        url = item.get("absolute_url")
        identity = job_id if job_id is not None else url
        if not identity or identity in seen:
            continue
        seen.add(identity)

        location_data = item.get("location") or {}
        record = {
            "url": url,
            "title": item.get("title"),
            "company": "Builder",
            "location": location_data.get("name"),
            "description": _plain_text(item.get("content")),
            "source": SITE,
            "external_id": str(job_id) if job_id is not None else None,
        }

        offices = item.get("offices") or []
        office_names = [
            office.get("name")
            for office in offices
            if isinstance(office, dict) and office.get("name")
        ]
        if office_names:
            record["locations"] = office_names

        departments = item.get("departments") or []
        department_names = [
            department.get("name")
            for department in departments
            if isinstance(department, dict) and department.get("name")
        ]
        if department_names:
            record["department"] = ", ".join(department_names)

        yield {
            field: value
            for field, value in record.items()
            if value is not None
        }
