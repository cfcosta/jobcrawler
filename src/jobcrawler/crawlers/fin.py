import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/intercom/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?i)<\s*(?:br|hr|/p|/div|/li|/h[1-6]|/tr|/section|/article)\s*/?\s*>",
        "\n",
        text,
    )
    text = re.sub(r"(?i)<\s*li(?:\s[^>]*)?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        line = " ".join(line.split())
        if line and (not lines or line != lines[-1]):
            lines.append(line)
    return "\n".join(lines)


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))

    for item in payload.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        description = _plain_text(item.get("content"))
        if not location or not description:
            continue

        departments = [
            department["name"]
            for department in item.get("departments") or []
            if department.get("name")
        ]
        locations = [
            office["name"]
            for office in item.get("offices") or []
            if office.get("name")
        ]

        job = {
            "source": SITE,
            "url": item["absolute_url"],
            "title": item["title"],
            "company": "Intercom",
            "location": location,
            "description": description,
            "external_id": str(item["id"]),
        }
        if locations:
            job["locations"] = locations
        if departments:
            job["department"] = ", ".join(departments)
        if item.get("first_published"):
            job["posted_at"] = item["first_published"]
        if item.get("application_deadline"):
            job["expires_at"] = item["application_deadline"]

        yield job
