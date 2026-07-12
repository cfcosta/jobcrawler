
import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/wekatest/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    text = html.unescape(value or "")
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(
        r"(?i)</?\s*(?:address|article|aside|blockquote|div|dl|dt|dd|"
        r"fieldset|figcaption|figure|footer|form|h[1-6]|header|hr|li|main|"
        r"nav|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)"
        r"(?:\s[^>]*)?>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _names(values):
    return [
        value["name"]
        for value in (values or [])
        if isinstance(value, dict) and value.get("name")
    ]


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for item in payload.get("jobs", []):
        departments = _names(item.get("departments"))
        offices = _names(item.get("offices"))
        fields = {
            "url": item.get("absolute_url"),
            "source": SITE,
            "title": item.get("title"),
            "company": (item.get("company_name") or "WEKA").strip(),
            "location": (item.get("location") or {}).get("name"),
            "description": _plain_text(item.get("content")),
            "external_id": str(item["id"]) if item.get("id") is not None else None,
            "department": ", ".join(departments) if departments else None,
            "locations": offices or None,
            "posted_at": item.get("first_published"),
            "expires_at": item.get("application_deadline"),
        }
        yield {key: value for key, value in fields.items() if value is not None}
