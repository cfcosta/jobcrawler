import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/beyondtrust/jobs?content=true"
EXTRA_HOSTS = []


def _description(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"<(script|style)\b[^>]*>.*?</\1\s*>",
        " ",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"</?(?:address|article|aside|blockquote|br|div|footer|h[1-6]|header|hr|li|main|ol|p|pre|section|table|td|th|tr|ul)\b[^>]*>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)

    lines = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for record in payload.get("jobs", []):
        title = record.get("title")
        location = (record.get("location") or {}).get("name")
        url = record.get("absolute_url")
        description = _description(record.get("content"))

        if not (title and location and url and description):
            continue

        job = {
            "url": url,
            "title": title,
            "company": record.get("company_name") or "BeyondTrust",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(record["id"]),
        }

        locations = [
            office["name"]
            for office in record.get("offices", [])
            if office.get("name")
        ]
        if locations:
            job["locations"] = locations

        departments = [
            department["name"]
            for department in record.get("departments", [])
            if department.get("name")
        ]
        if departments:
            job["department"] = departments[0]

        if record.get("first_published"):
            job["posted_at"] = record["first_published"]
        if record.get("application_deadline"):
            job["expires_at"] = record["application_deadline"]

        yield job
