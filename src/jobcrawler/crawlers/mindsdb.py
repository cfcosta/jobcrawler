import html
import json
import re
from jobcrawler.runtime.contract import Job

SITE = "https://boards-api.greenhouse.io/v1/boards/mindsdb/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    text = html.unescape(value or "")
    text = re.sub(r"<(script|style)\b[^>]*>.*?</\1\s*>", " ", text,
                  flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(
        r"</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|fieldset|"
        r"figcaption|figure|footer|form|h[1-6]|header|hr|li|main|nav|ol|p|"
        r"pre|section|table|tr|ul)\b[^>]*>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for posting in payload.get("jobs", []):
        title = posting.get("title")
        url = posting.get("absolute_url")
        location_data = posting.get("location") or {}
        location = location_data.get("name")
        description = _plain_text(posting.get("content"))

        if not title or not url or not location or not description:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "MindsDB",
            "location": location,
            "description": description,
            "external_id": str(posting["id"]) if posting.get("id") is not None else None,
        }

        offices = posting.get("offices") or []
        locations = [office.get("name") for office in offices if office.get("name")]
        if locations:
            fields["locations"] = locations

        departments = posting.get("departments") or []
        department_names = [item.get("name") for item in departments if item.get("name")]
        if department_names:
            fields["department"] = ", ".join(department_names)

        yield Job(**{key: value for key, value in fields.items() if value is not None})
