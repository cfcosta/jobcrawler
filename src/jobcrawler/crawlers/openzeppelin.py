import json
import re
from html import unescape


SITE = 'https://boards-api.greenhouse.io/v1/boards/openzeppelin/jobs?content=true'
EXTRA_HOSTS = []


def _html_to_text(value):
    text = unescape(value)
    text = re.sub(
        r"(?i)<(?:br|/p|/div|/li|/h[1-6]|/section|/article|/tr|/ul|/ol)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    lines = []
    for line in text.splitlines():
        cleaned = " ".join(unescape(line).split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for item in payload.get("jobs", []):
        job = {
            "url": item["absolute_url"],
            "title": item["title"],
            "company": "OpenZeppelin",
            "location": item["location"]["name"],
            "description": _html_to_text(item["content"]),
            "source": SITE,
            "external_id": str(item["id"]),
        }

        if item.get("first_published"):
            job["posted_at"] = item["first_published"]
        if item.get("application_deadline"):
            job["expires_at"] = item["application_deadline"]

        departments = [
            department["name"]
            for department in item.get("departments", [])
            if department.get("name")
        ]
        if departments:
            job["department"] = ", ".join(departments)

        locations = [
            office["name"]
            for office in item.get("offices", [])
            if office.get("name")
        ]
        if locations:
            job["locations"] = locations

        yield job
