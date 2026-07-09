
import json
import html
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/postman/jobs?content=true"
EXTRA_HOSTS = []


def clean_text(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h1|h2|h3|h4|tr|ul|ol|section)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def first_name(items):
    if isinstance(items, list) and items:
        names = []
        for item in items:
            if isinstance(item, dict) and item.get("name"):
                names.append(item.get("name"))
        if names:
            return ", ".join(names)
    return None


def put_if(job, key, value):
    if value is not None and value != "":
        job[key] = value


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for item in data.get("jobs", []):
        location = item.get("location") or {}
        if isinstance(location, dict):
            location_name = location.get("name")
        else:
            location_name = location

        job = {
            "url": item.get("absolute_url"),
            "title": item.get("title"),
            "company": item.get("company_name") or "Postman",
            "location": location_name,
            "description": clean_text(item.get("content")),
            "source": SITE,
        }

        put_if(job, "external_id", str(item.get("id")) if item.get("id") is not None else None)
        put_if(job, "department", first_name(item.get("departments")))
        put_if(job, "posted_at", item.get("first_published"))
        put_if(job, "expires_at", item.get("application_deadline"))
        put_if(job, "apply_url", item.get("absolute_url"))

        yield job
