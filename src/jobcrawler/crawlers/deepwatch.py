import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/deepwatchinc/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/h[1-6]|/tr|/section|/article)\s*/?\s*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)

    lines = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if line and (not lines or line != lines[-1]):
            lines.append(line)
    return "\n".join(lines)


def crawl(ctx):
    response = ctx.fetch(SITE)
    if isinstance(response, str):
        payload = json.loads(response)
    elif isinstance(response, bytes):
        payload = json.loads(response.decode("utf-8"))
    elif isinstance(response, dict):
        payload = response
    elif hasattr(response, "json"):
        payload = response.json()
    else:
        payload = json.loads(response.text)

    for item in payload.get("jobs", []):
        title = (item.get("title") or "").strip()
        location = (item.get("location") or {}).get("name")
        if isinstance(location, str):
            location = location.strip()

        locations = [
            office.get("name")
            for office in item.get("offices", [])
            if isinstance(office, dict) and office.get("name")
        ]
        departments = [
            department.get("name")
            for department in item.get("departments", [])
            if isinstance(department, dict) and department.get("name")
        ]

        fields = {
            "url": item.get("absolute_url"),
            "title": title,
            "company": item.get("company_name") or "Deepwatch",
            "location": location,
            "description": _plain_text(item.get("content")),
            "source": SITE,
            "external_id": str(item["id"]) if item.get("id") is not None else None,
            "posted_at": item.get("first_published"),
            "expires_at": item.get("application_deadline"),
            "department": ", ".join(departments) if departments else None,
            "locations": locations or None,
        }
        yield {key: value for key, value in fields.items() if value is not None}
