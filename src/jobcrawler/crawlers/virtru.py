
import json
import html
import re

SITE = 'https://boards-api.greenhouse.io/v1/boards/virtru/jobs?content=true'
EXTRA_HOSTS = []

def clean_html(value):
    text = html.unescape(value or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(?:p|div|li|h[1-6]|ul|ol|section)>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = text.replace("\r", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def nonempty_names(items):
    return [
        item["name"]
        for item in (items or [])
        if isinstance(item, dict) and item.get("name")
    ]

def metadata_value(item, name):
    for entry in item.get("metadata") or []:
        if entry.get("name") == name and entry.get("value"):
            return entry["value"]
    return None

def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for item in data.get("jobs") or []:
        location = (item.get("location") or {}).get("name")
        job = {
            "url": item.get("absolute_url"),
            "title": item.get("title"),
            "company": item.get("company_name") or "Virtru",
            "location": location,
            "description": clean_html(item.get("content")),
            "source": SITE,
            "external_id": str(item["id"]) if item.get("id") is not None else None,
            "employment_type": metadata_value(item, "Employment_Type"),
            "posted_at": item.get("first_published"),
            "expires_at": item.get("application_deadline"),
        }

        departments = nonempty_names(item.get("departments"))
        if departments:
            job["department"] = departments[0]

        offices = nonempty_names(item.get("offices"))
        if offices:
            job["locations"] = offices

        yield {key: value for key, value in job.items() if value is not None}
