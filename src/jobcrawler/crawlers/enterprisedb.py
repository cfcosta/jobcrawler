
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/edb/jobs?content=true"
EXTRA_HOSTS = []


def clean_description(value):
    value = html.unescape(value)
    value = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|dd|div|dl|dt|figcaption|figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for item in data["jobs"]:
        offices = [
            office["name"]
            for office in item.get("offices", [])
            if office.get("name")
        ]
        departments = [
            department["name"]
            for department in item.get("departments", [])
            if department.get("name")
        ]

        fields = {
            "url": item["absolute_url"],
            "title": item["title"],
            "company": "EDB",
            "location": item["location"]["name"],
            "description": clean_description(item["content"]),
            "source": SITE,
            "external_id": str(item["id"]),
            "posted_at": item.get("first_published"),
            "expires_at": item.get("application_deadline"),
            "locations": offices or None,
            "department": ", ".join(departments) if departments else None,
        }

        yield {
            key: value
            for key, value in fields.items()
            if value is not None
        }
