
import json
import re
import html

SITE = "https://boards-api.greenhouse.io/v1/boards/starburst/jobs?content=true"
EXTRA_HOSTS = []


def _clean_text(value):
    if not value:
        return None
    # Greenhouse returns the full body as HTML-entity-escaped HTML.
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|ul|ol|section|blockquote)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _names(items):
    out = []
    for item in items or []:
        if isinstance(item, dict):
            name = item.get("name")
            if name:
                out.append(str(name))
    return out


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        title = job.get("title")
        loc_obj = job.get("location") or {}
        location = loc_obj.get("name") if isinstance(loc_obj, dict) else None
        description = _clean_text(job.get("content"))
        url = job.get("absolute_url")
        if not (title and location and description and url):
            continue

        offices = _names(job.get("offices"))
        departments = _names(job.get("departments"))

        item = {
            "url": url,
            "title": title,
            "company": job.get("company_name") or "Starburst",
            "location": location,
            "description": description,
            "external_id": str(job.get("id")),
            "posted_at": job.get("first_published"),
            "source": SITE,
        }

        if offices:
            item["locations"] = offices
        if departments:
            item["department"] = departments[0]
        if job.get("application_deadline"):
            item["expires_at"] = job.get("application_deadline")
        if job.get("absolute_url"):
            item["apply_url"] = job.get("absolute_url")

        yield item
