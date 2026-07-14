import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/hiddenlayer/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    value = html.unescape(value)
    value = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|dd|div|dl|dt|figcaption|figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)(?:\s[^>]*)?>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        clean = " ".join(line.split())
        if clean:
            lines.append(clean)
    return "\n".join(lines)


def crawl(ctx):
    response = ctx.fetch(SITE)
    payload = json.loads(response)

    for posting in payload.get("jobs", []):
        location = (posting.get("location") or {}).get("name")
        departments = [
            item["name"] for item in posting.get("departments", [])
            if item.get("name")
        ]
        offices = [
            item["name"] for item in posting.get("offices", [])
            if item.get("name")
        ]

        fields = {
            "url": posting["absolute_url"],
            "title": posting["title"],
            "company": "HiddenLayer",
            "source": "Greenhouse",
            "location": location,
            "description": _plain_text(posting["content"]),
            "external_id": str(posting["id"]),
        }
        if departments:
            fields["department"] = ", ".join(departments)
        if offices:
            fields["locations"] = offices

        yield fields
