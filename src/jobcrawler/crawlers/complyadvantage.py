
import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/complyadvantage/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    markup = html.unescape(value or "")
    markup = re.sub(
        r"<\s*(script|style)\b[^>]*>.*?<\s*/\s*\1\s*>",
        "",
        markup,
        flags=re.IGNORECASE | re.DOTALL,
    )
    markup = re.sub(
        r"<\s*li\b[^>]*>",
        "\n- ",
        markup,
        flags=re.IGNORECASE,
    )
    markup = re.sub(
        r"<\s*(?:br|hr)\b[^>]*>",
        "\n",
        markup,
        flags=re.IGNORECASE,
    )
    markup = re.sub(
        r"<\s*/?\s*(?:address|article|aside|blockquote|dd|div|dl|dt|"
        r"figcaption|figure|footer|h[1-6]|header|li|main|nav|ol|p|pre|"
        r"section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        markup,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", "", markup)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def crawl(ctx):
    response = ctx.fetch(SITE)
    if isinstance(response, bytes):
        response = response.decode("utf-8")
    data = json.loads(response)

    for item in data.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        departments = [
            entry["name"]
            for entry in item.get("departments", [])
            if entry.get("name")
        ]
        locations = [
            entry["name"]
            for entry in item.get("offices", [])
            if entry.get("name")
        ]

        fields = {
            "url": item.get("absolute_url"),
            "source": SITE,
            "apply_url": item.get("absolute_url"),
            "title": (item.get("title") or "").strip(),
            "company": "ComplyAdvantage",
            "location": location,
            "description": _plain_text(item.get("content")),
            "external_id": str(item["id"]) if item.get("id") is not None else None,
            "posted_at": item.get("first_published"),
            "expires_at": item.get("application_deadline"),
            "department": ", ".join(departments) if departments else None,
            "locations": locations or None,
        }
        yield {key: value for key, value in fields.items() if value is not None}
