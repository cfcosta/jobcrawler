
import html
import json
import re



SITE = "https://boards-api.greenhouse.io/v1/boards/mozilla/jobs?content=true"
EXTRA_HOSTS = []


def _description(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?i)<\s*(?:br|hr)\s*/?\s*>|</?\s*(?:address|article|aside|blockquote|div|h[1-6]|header|li|main|ol|p|pre|section|table|td|th|tr|ul)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        normalized = " ".join(line.split())
        if normalized and (not lines or normalized != lines[-1]):
            lines.append(normalized)
    return "\n".join(lines)


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs", []):
        absolute_url = posting.get("absolute_url")
        kwargs = {
            "url": absolute_url,
            "title": posting.get("title"),
            "company": "Mozilla",
            "source": "Greenhouse",
            "location": (posting.get("location") or {}).get("name"),
            "description": _description(posting.get("content")),
            "external_id": str(posting["id"]),
            "apply_url": absolute_url,
        }

        offices = [
            office["name"]
            for office in posting.get("offices", [])
            if office.get("name")
        ]
        if offices:
            kwargs["locations"] = offices

        departments = [
            department["name"]
            for department in posting.get("departments", [])
            if department.get("name")
        ]
        if departments:
            kwargs["department"] = ", ".join(departments)

        if posting.get("first_published"):
            kwargs["posted_at"] = posting["first_published"]
        if posting.get("application_deadline"):
            kwargs["expires_at"] = posting["application_deadline"]

        yield kwargs
