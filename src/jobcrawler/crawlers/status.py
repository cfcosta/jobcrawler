import json
import re
from html import unescape

SITE = "https://boards-api.greenhouse.io/v1/boards/logos/jobs?content=true"
EXTRA_HOSTS = []

def clean_html(value):
    text = unescape(value or "")
    text = re.sub(r"(?is)<(?:script|style)[^>]*>.*?</(?:script|style)>", "", text)
    text = re.sub(r"(?i)<br\s*/?>|</?(?:p|div|h[1-6]|li|ul|ol|section|blockquote|pre)[^>]*>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = unescape(text)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)

def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for item in data.get("jobs", []):
        job = {
            "source": SITE,
            "url": item.get("absolute_url"),
            "title": item.get("title"),
            "company": item.get("company_name") or "Logos",
            "location": (item.get("location") or {}).get("name"),
            "description": clean_html(item.get("content")),
            "external_id": str(item["id"]) if item.get("id") is not None else None,
            "posted_at": item.get("first_published"),
        }
        offices = [x.get("name") for x in item.get("offices", []) if x.get("name")]
        departments = [x.get("name") for x in item.get("departments", []) if x.get("name")]
        if offices:
            job["locations"] = offices
        if departments:
            job["department"] = ", ".join(departments)
        if item.get("application_deadline"):
            job["expires_at"] = item["application_deadline"]
        yield {key: value for key, value in job.items() if value is not None}
