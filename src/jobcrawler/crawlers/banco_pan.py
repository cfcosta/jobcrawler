
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/bancopan/jobs?content=true"
EXTRA_HOSTS = []


def _description(value):
    text = html.unescape(value or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)<li(?:\s[^>]*)?>", "\n- ", text)
    text = re.sub(
        r"(?i)</?(?:p|div|section|article|h[1-6]|ul|ol|table|tr|td|th|blockquote|pre)(?:\s[^>]*)?>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    lines = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def _present(value):
    if value is None or value == "":
        return None
    return value


def crawl(ctx):
    response = ctx.fetch(SITE)
    payload = json.loads(response)
    seen = set()

    for record in payload.get("jobs", []):
        job_id = record.get("id")
        if job_id is None or job_id in seen:
            continue
        seen.add(job_id)

        title = str(record.get("title") or "").strip()
        location = _present((record.get("location") or {}).get("name"))
        description = _description(record.get("content"))
        url = _present(record.get("absolute_url"))
        if not title or not location or not description or not url:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Banco PAN",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(job_id),
        }

        posted_at = _present(record.get("first_published"))
        if posted_at is not None:
            fields["posted_at"] = posted_at

        expires_at = _present(record.get("application_deadline"))
        if expires_at is not None:
            fields["expires_at"] = expires_at

        locations = []
        for office in record.get("offices") or []:
            if isinstance(office, dict) and _present(office.get("name")) is not None:
                locations.append(office["name"])
        if locations:
            fields["locations"] = locations

        departments = []
        for department in record.get("departments") or []:
            if isinstance(department, dict) and _present(department.get("name")) is not None:
                departments.append(department["name"])
        if departments:
            fields["department"] = ", ".join(departments)

        yield fields
