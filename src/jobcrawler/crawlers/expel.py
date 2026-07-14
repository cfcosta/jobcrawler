import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/expel/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(encoded_html):
    text = html.unescape(encoded_html)
    text = re.sub(
        r"(?i)<\s*(?:br|hr|/p|/div|/li|/h[1-6]|/tr|/section|/article)\s*/?\s*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for posting in payload.get("jobs", []):
        location = posting.get("location") or {}
        job = {
            "url": posting["absolute_url"],
            "title": posting["title"],
            "company": posting.get("company_name") or "Expel",
            "location": location.get("name"),
            "description": _plain_text(posting["content"]),
            "source": "greenhouse",
            "external_id": str(posting["id"]),
        }

        offices = [
            office["name"]
            for office in posting.get("offices", [])
            if office.get("name")
        ]
        if offices:
            job["locations"] = offices

        departments = [
            department["name"]
            for department in posting.get("departments", [])
            if department.get("name")
        ]
        if departments:
            job["department"] = ", ".join(departments)

        if posting.get("first_published"):
            job["posted_at"] = posting["first_published"]
        if posting.get("application_deadline"):
            job["expires_at"] = posting["application_deadline"]

        yield job
