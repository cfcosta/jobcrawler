
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/greynoiseintelligence/jobs?content=true"
EXTRA_HOSTS = []


def _text(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?is)<(script|style)\b[^>]*>.*?</\1\s*>",
        "",
        text,
    )
    text = re.sub(r"(?i)<br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)<li\b[^>]*>", "\n- ", text)
    text = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|div|dl|dt|dd|fieldset|"
        r"figcaption|figure|footer|form|h[1-6]|header|hr|main|nav|ol|p|pre|"
        r"section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))
    for item in payload.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        departments = [
            department["name"]
            for department in item.get("departments", [])
            if department.get("name")
        ]
        offices = [
            office["name"]
            for office in item.get("offices", [])
            if office.get("name")
        ]
        url = item.get("absolute_url")

        yield {
            "url": url,
            "source": SITE,
            "title": item.get("title"),
            "company": "GreyNoise Intelligence",
            "location": location,
            "locations": offices or None,
            "description": _text(item.get("content")),
            "department": ", ".join(departments) if departments else None,
            "posted_at": item.get("first_published"),
            "expires_at": item.get("application_deadline"),
            "external_id": str(item["id"]) if item.get("id") is not None else None,
            "apply_url": url,
        }
