import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/validationcloud/jobs?content=true"
EXTRA_HOSTS = []


def _html_to_text(value):
    value = html.unescape(value)
    value = re.sub(
        r"(?is)<(?:script|style|template|noscript|svg)\b[^>]*>.*?</(?:script|style|template|noscript|svg)\s*>",
        "",
        value,
    )
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|dd|div|dl|dt|figcaption|figure|h[1-6]|hr|li|ol|p|pre|section|table|td|th|tr|ul)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?s)<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for item in payload.get("jobs", []):
        url = item.get("absolute_url")
        title = item.get("title")
        location = (item.get("location") or {}).get("name")
        description = _html_to_text(item.get("content") or "")

        if not url or not title or not location or not description:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Validation Cloud",
            "location": location,
            "description": description,
            "source": SITE,
        }

        if item.get("id") is not None:
            fields["external_id"] = str(item["id"])
        if item.get("first_published"):
            fields["posted_at"] = item["first_published"]
        if item.get("application_deadline"):
            fields["expires_at"] = item["application_deadline"]

        departments = [
            department.get("name")
            for department in item.get("departments", [])
            if department.get("name")
        ]
        if departments:
            fields["department"] = departments[0]

        yield fields
