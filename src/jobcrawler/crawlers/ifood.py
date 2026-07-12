
import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/ifoodcarreiras/jobs?content=true"
EXTRA_HOSTS = []


def _clean_description(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?i)<\s*(?:br|hr)\s*/?\s*>|</?\s*(?:address|article|aside|blockquote|div|dl|dt|dd|figcaption|figure|footer|h[1-6]|header|li|main|nav|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)\s*>", "", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = html.unescape(text)
    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _metadata(job, name):
    for item in job.get("metadata") or []:
        if item.get("name") == name and item.get("value") not in (None, ""):
            return str(item["value"])
    return None


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for item in data.get("jobs") or []:
        url = item.get("absolute_url")
        title = item.get("title")
        location = (item.get("location") or {}).get("name")
        description = _clean_description(item.get("content"))

        if not url or not title or not location or not description:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": item.get("company_name") or "iFood",
            "location": location,
            "description": description,
            "source": "Greenhouse",
            "external_id": str(item["id"]) if item.get("id") is not None else None,
            "apply_url": url,
            "posted_at": item.get("first_published"),
            "expires_at": item.get("application_deadline"),
            "employment_type": _metadata(item, "Tipo de contrato"),
        }

        departments = [
            department.get("name")
            for department in item.get("departments") or []
            if department.get("name")
        ]
        if departments:
            fields["department"] = departments[0]

        locations = [
            office.get("name")
            for office in item.get("offices") or []
            if office.get("name")
        ]
        if locations:
            fields["locations"] = locations

        yield {
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        }
