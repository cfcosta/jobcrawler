
import html
import json
import re

SITE = 'https://boards-api.greenhouse.io/v1/boards/customerio/jobs?content=true'
EXTRA_HOSTS = []


def _text(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"</(?:p|div|h[1-6]|li|ul|ol|section)>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip() or None


def crawl(ctx):
    response = ctx.fetch(SITE)
    data = json.loads(response)

    for posting in data.get("jobs", []):
        location = (posting.get("location") or {}).get("name")
        description = _text(posting.get("content"))
        if not posting.get("title") or not location or not description:
            continue

        item = {
            "url": posting["absolute_url"],
            "apply_url": posting["absolute_url"],
            "title": posting["title"],
            "company": "Customer.io",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(posting["id"]),
        }

        offices = [
            office.get("name")
            for office in posting.get("offices") or []
            if office.get("name")
        ]
        if offices:
            item["locations"] = offices

        departments = [
            department.get("name")
            for department in posting.get("departments") or []
            if department.get("name")
        ]
        if departments:
            item["department"] = departments[0]

        if posting.get("first_published"):
            item["posted_at"] = posting["first_published"]
        if posting.get("application_deadline"):
            item["expires_at"] = posting["application_deadline"]

        yield item
