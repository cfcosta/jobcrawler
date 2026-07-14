
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/mxtechnologiesinc/jobs?content=true"
EXTRA_HOSTS = []


def _text(value):
    value = html.unescape(value or "")
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(?:p|div|li|h[1-6]|ul|ol)>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for item in data.get("jobs", []):
        location = (item.get("location") or {}).get("name")
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

        kwargs = {
            "url": item["absolute_url"],
            "title": item["title"],
            "company": "MX Technologies",
            "location": location,
            "description": _text(item.get("content")),
            "source": SITE,
            "external_id": str(item["id"]),
        }
        if offices:
            kwargs["locations"] = offices
        if departments:
            kwargs["department"] = ", ".join(departments)
        if item.get("first_published"):
            kwargs["posted_at"] = item["first_published"]
        if item.get("application_deadline"):
            kwargs["expires_at"] = item["application_deadline"]

        yield kwargs
