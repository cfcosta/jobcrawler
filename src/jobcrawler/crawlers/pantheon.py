
import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/pantheon/jobs?content=true"
EXTRA_HOSTS = []


def _html_to_text(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?is)<(?:script|style|template|noscript)\b[^>]*>.*?</(?:script|style|template|noscript)\s*>",
        "",
        text,
    )
    text = re.sub(
        r"(?i)<(?:br|/p|/div|/li|/h[1-6]|/tr|/section|/article|/ul|/ol)\s*/?>",
        "\n",
        text,
    )
    text = re.sub(r"(?i)<li\b[^>]*>", "- ", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _names(items):
    result = []
    for item in items or []:
        name = item.get("name")
        if name and name not in result:
            result.append(name)
    return result


def crawl(ctx):
    response = ctx.fetch(SITE + "&pay_transparency=true")
    payload = json.loads(response)

    for item in payload.get("jobs", []):
        departments = _names(item.get("departments"))
        locations = _names(item.get("offices"))

        fields = {
            "url": item.get("absolute_url"),
            "source": SITE,
            "title": item.get("title"),
            "company": "Pantheon",
            "location": (item.get("location") or {}).get("name"),
            "description": _html_to_text(item.get("content")),
            "external_id": str(item["id"]) if item.get("id") is not None else None,
            "posted_at": item.get("first_published"),
            "department": ", ".join(departments) if departments else None,
            "locations": locations or None,
        }

        yield {key: value for key, value in fields.items() if value is not None}
