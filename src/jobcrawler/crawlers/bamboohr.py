import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/bamboohr17/jobs?content=true"
EXTRA_HOSTS = []


def _html_to_text(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|tr|h[1-6]|ul|ol)\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li[^>]*>", "\n- ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _first_name(items):
    for item in items or []:
        if isinstance(item, dict) and item.get("name"):
            return item.get("name")
    return None


def _locations(name):
    """BambooHR encodes multi-region roles as a ';'-joined location string."""
    if not name:
        return None
    parts = [p.strip() for p in name.split(";")]
    parts = [p for p in parts if p]
    return parts if len(parts) > 1 else None


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        location = (job.get("location") or {}).get("name")
        item = {
            "url": job.get("absolute_url"),
            "title": job.get("title"),
            "company": job.get("company_name") or "BambooHR",
            "location": location,
            "description": _html_to_text(job.get("content")),
            "source": SITE,
            "external_id": str(job.get("id")) if job.get("id") is not None else None,
            "apply_url": job.get("absolute_url"),
            "posted_at": job.get("first_published"),
            "department": _first_name(job.get("departments")),
        }

        locations = _locations(location)
        if locations:
            item["locations"] = locations

        deadline = job.get("application_deadline")
        if deadline:
            item["expires_at"] = deadline

        if item.get("url") and item.get("title") and item.get("description"):
            yield {key: value for key, value in item.items() if value is not None}
