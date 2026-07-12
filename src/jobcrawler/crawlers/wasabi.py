import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/wasabi/jobs?content=true"
EXTRA_HOSTS = []


def _html_to_text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?is)<(?:br|hr)\s*/?>|</(?:address|article|aside|blockquote|div|"
        r"h[1-6]|header|li|main|nav|ol|p|pre|section|table|td|th|tr|ul)>",
        "\n",
        value,
    )
    value = re.sub(r"(?is)<script\b[^>]*>.*?</script>", "", value)
    value = re.sub(r"(?is)<style\b[^>]*>.*?</style>", "", value)
    value = re.sub(r"(?s)<[^>]+>", "", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        normalized = " ".join(line.split())
        if normalized:
            lines.append(normalized)
    return "\n".join(lines)


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for posting in payload.get("jobs", []):
        location = (posting.get("location") or {}).get("name")
        offices = [
            office["name"]
            for office in posting.get("offices", [])
            if office.get("name")
        ]
        departments = [
            department["name"]
            for department in posting.get("departments", [])
            if department.get("name")
        ]

        fields = {
            "source": SITE,
            "url": posting.get("absolute_url"),
            "title": posting.get("title"),
            "company": "Wasabi Technologies",
            "location": location,
            "description": _html_to_text(posting.get("content")),
            "external_id": (
                str(posting["id"]) if posting.get("id") is not None else None
            ),
            "locations": offices or None,
            "department": ", ".join(departments) if departments else None,
        }
        yield {
            key: value
            for key, value in fields.items()
            if value is not None
        }
