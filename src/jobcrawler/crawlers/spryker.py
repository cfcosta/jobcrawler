import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/spryker/jobs?content=true"
EXTRA_HOSTS = []


def _description(value):
    value = html.unescape(value)
    value = re.sub(
        r"(?i)<\s*(?:br|hr)\s*/?\s*>|</?\s*(?:address|article|aside|blockquote|div|footer|h[1-6]|header|li|main|ol|p|pre|section|table|tr|ul)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)\s*>", "", value)
    value = re.sub(r"(?s)<[^>]+>", "", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _workplace_type(metadata):
    for item in metadata or []:
        if (item.get("name") or "").strip().lower() != "workplace type":
            continue
        value = item.get("value")
        if isinstance(value, list):
            values = [str(part).strip() for part in value if str(part).strip()]
            return ", ".join(values) or None
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for record in payload.get("jobs", []):
        location = (record.get("location") or {}).get("name")
        content = record.get("content")
        if not record.get("title") or not location or not content:
            continue

        kwargs = {
            "url": record["absolute_url"],
            "title": record["title"],
            "company": "Spryker",
            "location": location,
            "description": _description(content),
            "source": SITE,
            "external_id": str(record["id"]),
        }

        offices = [
            office["name"]
            for office in record.get("offices", [])
            if office.get("name")
        ]
        if offices:
            kwargs["locations"] = offices

        departments = [
            department["name"]
            for department in record.get("departments", [])
            if department.get("name")
        ]
        if departments:
            kwargs["department"] = ", ".join(departments)

        workplace_type = _workplace_type(record.get("metadata"))
        if workplace_type:
            kwargs["workplace_type"] = workplace_type

        if record.get("first_published"):
            kwargs["posted_at"] = record["first_published"]
        if record.get("application_deadline"):
            kwargs["expires_at"] = record["application_deadline"]

        yield kwargs
