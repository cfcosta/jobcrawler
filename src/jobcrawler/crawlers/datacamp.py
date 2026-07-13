
import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/datacamp/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?i)<\s*(?:br|p|div|li|h[1-6]|tr|section|article|ul|ol)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(
        r"(?i)</\s*(?:p|div|li|h[1-6]|tr|section|article|ul|ol)\s*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for item in data.get("jobs", []):
        yield {
            "url": item["absolute_url"],
            "title": item["title"],
            "company": "DataCamp",
            "location": (item.get("location") or {}).get("name"),
            "description": _plain_text(item.get("content")),
            "source": SITE,
            "external_id": str(item["id"]),
        }
