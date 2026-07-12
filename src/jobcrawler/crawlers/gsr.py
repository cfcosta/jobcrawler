import html
import json
import re

SITE = 'https://boards-api.greenhouse.io/v1/boards/gsrmarkets/jobs?content=true'
EXTRA_HOSTS = []


def _clean_description(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"<\s*(?:br|/p|/div|/li|/ul|/ol|/h[1-6])\s*/?\s*>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for item in payload.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        job = {
            "url": item["absolute_url"],
            "title": item["title"],
            "company": "GSR Markets",
            "location": location,
            "description": _clean_description(item.get("content")),
            "source": SITE,
            "external_id": str(item["id"]),
        }

        offices = [
            office["name"]
            for office in item.get("offices", [])
            if office.get("name")
        ]
        if offices:
            job["locations"] = offices

        departments = [
            department["name"]
            for department in item.get("departments", [])
            if department.get("name")
        ]
        if departments:
            job["department"] = "; ".join(departments)

        if item.get("first_published"):
            job["posted_at"] = item["first_published"]
        if item.get("application_deadline"):
            job["expires_at"] = item["application_deadline"]

        yield job
