import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/comet/jobs?content=true"
EXTRA_HOSTS = []


def Job(**fields):
    return fields


def _plain_text(value):
    value = html.unescape(value)
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)<\s*li(?:\s[^>]*)?>", "\n- ", value)
    value = re.sub(
        r"(?i)</?\s*(?:p|div|section|article|header|footer|h[1-6]|ul|ol|"
        r"blockquote|pre|table|tr|dl|dt|dd)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def crawl(ctx):
    response = ctx.fetch(SITE)
    payload = json.loads(response)

    for posting in payload.get("jobs", []):
        location_data = posting.get("location") or {}
        departments = [
            item["name"]
            for item in posting.get("departments", [])
            if item.get("name")
        ]
        offices = [
            item["name"]
            for item in posting.get("offices", [])
            if item.get("name")
        ]

        fields = {
            "url": posting["absolute_url"],
            "source": SITE,
            "title": posting["title"],
            "company": "Comet",
            "location": location_data.get("name"),
            "description": _plain_text(posting.get("content") or ""),
            "external_id": str(posting["id"]),
            "posted_at": posting.get("first_published"),
            "expires_at": posting.get("application_deadline"),
        }
        if offices:
            fields["locations"] = offices
        if departments:
            fields["department"] = ", ".join(departments)

        yield Job(**{
            key: value for key, value in fields.items()
            if value is not None
        })
