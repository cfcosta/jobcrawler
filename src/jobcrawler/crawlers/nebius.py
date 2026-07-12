import html
import json
import re


SITE = 'https://boards-api.greenhouse.io/v1/boards/nebius/jobs?content=true'
EXTRA_HOSTS = []


def _plain_text(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?i)<br\\s*/?>|</?(?:address|article|aside|blockquote|div|footer|h[1-6]|header|hr|li|main|ol|p|pre|section|table|tr|ul)\\b[^>]*>",
        "\\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def crawl(ctx):
    response = ctx.fetch(SITE)
    data = json.loads(response)

    for posting in data.get("jobs", []):
        location = (posting.get("location") or {}).get("name")
        description = _plain_text(posting.get("content"))
        title = (posting.get("title") or "").strip()
        url = posting.get("absolute_url")
        if not title or not url or not location or not description:
            continue

        kwargs = {
            "url": url,
            "source": SITE,
            "title": title,
            "company": "Nebius",
            "location": location,
            "description": description,
            "external_id": str(posting["id"]),
            "apply_url": url,
        }

        posted_at = posting.get("first_published")
        if posted_at:
            kwargs["posted_at"] = posted_at

        locations = [
            office["name"]
            for office in posting.get("offices") or []
            if office.get("name")
        ]
        if locations:
            kwargs["locations"] = locations

        departments = [
            department["name"]
            for department in posting.get("departments") or []
            if department.get("name")
        ]
        if departments:
            kwargs["department"] = ", ".join(departments)

        yield kwargs
