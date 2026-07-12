import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/aerospike/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    text = html.unescape(value or "")
    text = re.sub(r"(?i)<br\s*/?>|</(?:p|div|li|h[1-6]|tr|section)>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))

    for item in payload.get("jobs", []):
        title = (item.get("title") or "").strip()
        location = (item.get("location") or {}).get("name")
        description = _plain_text(item.get("content"))
        url = item.get("absolute_url")

        if not title or not location or not description or not url:
            continue

        fields = {
            "url": url,
            "source": SITE,
            "title": title,
            "company": "Aerospike",
            "location": location,
            "description": description,
            "external_id": str(item["id"]),
            "apply_url": url,
        }

        offices = [
            office["name"]
            for office in item.get("offices", [])
            if office.get("name")
        ]
        if offices:
            fields["locations"] = offices

        departments = [
            department["name"]
            for department in item.get("departments", [])
            if department.get("name")
        ]
        if departments:
            fields["department"] = ", ".join(departments)

        if item.get("first_published"):
            fields["posted_at"] = item["first_published"]

        if item.get("application_deadline"):
            fields["expires_at"] = item["application_deadline"]

        yield fields
