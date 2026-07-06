
import json
import html
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/modulrfinance/jobs?content=true"


def _clean_text(value):
    if not value:
        return None

    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|tr|section)\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li\b[^>]*>", "\n- ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return text or None


def _first_name(items):
    if isinstance(items, list) and items:
        first = items[0] or {}
        if isinstance(first, dict):
            return first.get("name") or None
    return None


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        title = job.get("title")
        url = job.get("absolute_url")
        location = (job.get("location") or {}).get("name")
        description = _clean_text(job.get("content"))
        if not (title and url and location and description):
            continue

        item = {
            "url": url,
            "title": title,
            "company": job.get("company_name") or "Modulr",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(job.get("id")),
        }

        department = _first_name(job.get("departments"))
        if department:
            item["department"] = department

        if job.get("first_published"):
            item["posted_at"] = job.get("first_published")
        if job.get("application_deadline"):
            item["expires_at"] = job.get("application_deadline")

        yield item
