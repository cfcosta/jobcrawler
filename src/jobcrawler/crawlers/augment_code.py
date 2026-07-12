
import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/augmentcomputing/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(markup):
    text = html.unescape(markup or "")
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(
        r"(?i)</?\s*(?:address|article|aside|blockquote|div|h[1-6]|li|ol|p|pre|section|table|td|th|tr|ul)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for posting in payload.get("jobs", []):
        title = posting.get("title")
        location = (posting.get("location") or {}).get("name")
        description = _plain_text(posting.get("content"))
        url = posting.get("absolute_url")

        if not title or not location or not description or not url:
            continue

        item = {
            "url": url,
            "title": title,
            "company": "Augment Code",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(posting["id"]),
        }

        departments = [
            department["name"]
            for department in posting.get("departments", [])
            if department.get("name")
        ]
        if departments:
            item["department"] = ", ".join(departments)

        offices = [
            office["name"]
            for office in posting.get("offices", [])
            if office.get("name")
        ]
        if offices:
            item["locations"] = offices

        if posting.get("first_published"):
            item["posted_at"] = posting["first_published"]

        if posting.get("application_deadline"):
            item["expires_at"] = posting["application_deadline"]

        yield item
