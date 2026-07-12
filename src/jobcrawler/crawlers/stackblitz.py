
import html
import json
import re

SITE = 'https://boards-api.greenhouse.io/v1/boards/stackblitz/jobs?content=true'
EXTRA_HOSTS = []


def _description(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?i)<(?:br|/p|/div|/li|/h[1-6]|/tr|/section|/article|/blockquote|/ul|/ol)[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        clean = " ".join(line.split())
        if clean:
            lines.append(clean)
    return "\n".join(lines)


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))
    for posting in payload.get("jobs", []):
        title = (posting.get("title") or "").strip()
        location = (posting.get("location") or {}).get("name")
        description = _description(posting.get("content"))

        if not title or not location or not description:
            continue

        item = {
            "url": posting["absolute_url"],
            "title": title,
            "company": "StackBlitz",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(posting["id"]),
        }

        offices = [
            office.get("name")
            for office in posting.get("offices", [])
            if office.get("name")
        ]
        if offices:
            item["locations"] = offices

        departments = [
            department.get("name")
            for department in posting.get("departments", [])
            if department.get("name")
        ]
        if departments:
            item["department"] = ", ".join(departments)

        if posting.get("first_published"):
            item["posted_at"] = posting["first_published"]
        if posting.get("application_deadline"):
            item["expires_at"] = posting["application_deadline"]

        yield item
