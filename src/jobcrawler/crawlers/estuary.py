
import json
import re
import html

SITE = "https://estuary.dev/page-data/about/page-data.json"
EXTRA_HOSTS = []


def _html_to_text(markup):
    if not markup:
        return None

    text = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1>", " ", markup)
    text = re.sub(
        r"(?i)</?(p|div|br|li|ul|ol|h[1-6]|section|article|blockquote|tr|td|th|table|header|footer)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text).replace("\xa0", " ")

    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines) or None


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))
    nodes = (
        payload.get("result", {})
        .get("data", {})
        .get("allStrapiJobPosting", {})
        .get("nodes", [])
    )

    for node in nodes:
        slug = node.get("slug")
        title = node.get("title")
        location = node.get("location")
        html_body = (
            ((node.get("description") or {}).get("data") or {})
            .get("childHtmlRehype", {})
            .get("html")
        )
        description = _html_to_text(html_body)

        if not (slug and title and location and description):
            continue

        yield {
            "url": "https://estuary.dev/about/#" + slug,
            "title": title,
            "company": "Estuary",
            "location": location,
            "description": description,
            "external_id": slug,
            "source": SITE,
        }
