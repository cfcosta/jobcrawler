
import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/acquia/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/ul|/ol|/h[1-6]|/section|/tr)\s*/?\s*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for posting in payload.get("jobs", []):
        location = (posting.get("location") or {}).get("name")
        departments = [
            item.get("name")
            for item in posting.get("departments", [])
            if item.get("name")
        ]
        locations = [
            item.get("name")
            for item in posting.get("offices", [])
            if item.get("name")
        ]

        fields = {
            "url": posting.get("absolute_url"),
            "title": posting.get("title"),
            "company": posting.get("company_name") or "Acquia",
            "location": location,
            "description": _plain_text(posting.get("content")),
            "external_id": str(posting.get("id")),
            "source": SITE,
        }
        if locations:
            fields["locations"] = locations
        if departments:
            fields["department"] = ", ".join(departments)
        if posting.get("first_published"):
            fields["posted_at"] = posting.get("first_published")
        if posting.get("application_deadline"):
            fields["expires_at"] = posting.get("application_deadline")

        yield fields
