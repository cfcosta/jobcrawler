
import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/immunefi/jobs?content=true"
EXTRA_HOSTS = []


def _description(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"<\s*(?:script|style|noscript)\b[^>]*>.*?<\s*/\s*(?:script|style|noscript)\s*>",
        " ",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<\s*li\b[^>]*>", "\n- ", text, flags=re.IGNORECASE)
    text = re.sub(
        r"<\s*/?\s*(?:address|article|aside|blockquote|br|div|dl|dt|dd|"
        r"figcaption|figure|footer|h[1-6]|header|hr|main|ol|p|pre|section|"
        r"table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text).replace("\xa0", " ")

    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))
    for record in payload.get("jobs", []):
        location = (record.get("location") or {}).get("name")
        offices = [
            office["name"]
            for office in (record.get("offices") or [])
            if office.get("name")
        ]
        departments = [
            department["name"]
            for department in (record.get("departments") or [])
            if department.get("name")
        ]
        url = record.get("absolute_url")

        fields = {
            "url": url,
            "title": record.get("title"),
            "company": record.get("company_name") or "Immunefi",
            "location": location,
            "description": _description(record.get("content")),
            "source": "Greenhouse",
            "external_id": str(record["id"]) if record.get("id") is not None else None,
            "apply_url": url,
            "posted_at": record.get("first_published"),
            "expires_at": record.get("application_deadline"),
            "locations": offices or None,
            "department": ", ".join(departments) if departments else None,
        }
        yield {key: value for key, value in fields.items() if value is not None}
