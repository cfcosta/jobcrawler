
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/figment/jobs?content=true"
EXTRA_HOSTS = []


def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|section|article|tr)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _first_name(items):
    if not isinstance(items, list):
        return None
    names = []
    for item in items:
        if isinstance(item, dict) and item.get("name"):
            names.append(str(item["name"]).strip())
    return ", ".join(n for n in names if n) or None


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        title = (job.get("title") or "").strip()
        url = (job.get("absolute_url") or "").strip()
        location = ""
        if isinstance(job.get("location"), dict):
            location = (job["location"].get("name") or "").strip()
        description = _clean_html(job.get("content"))

        if not (title and url and location and description):
            continue

        item = {
            "url": url,
            "title": title,
            "company": "Figment",
            "location": location,
            "description": description,
            "source": SITE,
        }

        external_id = job.get("id") or job.get("requisition_id") or job.get("internal_job_id")
        if external_id is not None:
            item["external_id"] = str(external_id)

        posted_at = job.get("first_published")
        if posted_at:
            item["posted_at"] = posted_at

        expires_at = job.get("application_deadline")
        if expires_at:
            item["expires_at"] = expires_at

        department = _first_name(job.get("departments"))
        if department:
            item["department"] = department

        yield item
