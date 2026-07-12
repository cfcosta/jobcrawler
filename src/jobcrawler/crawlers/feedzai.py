import html
import json
import re


SITE = 'https://boards-api.greenhouse.io/v1/boards/feedzai/jobs?content=true'
EXTRA_HOSTS = []


def _plain_text(value):
    value = html.unescape(value)
    value = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/h[1-6]|/tr|/section|/article|/ul|/ol)\s*/?\s*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        clean = " ".join(line.split())
        if clean:
            lines.append(clean)
    return "\n".join(lines)


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for item in payload["jobs"]:
        departments = [
            entry["name"].strip()
            for entry in item.get("departments", [])
            if entry.get("name", "").strip()
        ]
        locations = [
            entry["name"].strip()
            for entry in item.get("offices", [])
            if entry.get("name", "").strip()
        ]

        fields = {
            "url": item["absolute_url"],
            "title": item["title"].strip(),
            "company": item.get("company_name") or "Feedzai",
            "location": item["location"]["name"].strip(),
            "description": _plain_text(item["content"]),
            "source": "Greenhouse",
            "external_id": str(item["id"]),
            "apply_url": item["absolute_url"],
        }

        if locations:
            fields["locations"] = locations
        if departments:
            fields["department"] = ", ".join(departments)
        if item.get("first_published"):
            fields["posted_at"] = item["first_published"]
        if item.get("application_deadline"):
            fields["expires_at"] = item["application_deadline"]

        yield fields
