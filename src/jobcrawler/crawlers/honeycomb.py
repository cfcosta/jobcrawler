
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/honeycomb/jobs?content=true"
EXTRA_HOSTS = []


_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_RE = re.compile(r"\n\s*\n+")


def _clean_description(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|section|tr)\s*>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = _SPACE_RE.sub(" ", text)
    text = _BLANK_RE.sub("\n\n", text)
    return text.strip() or None


def _first_department(job):
    departments = job.get("departments") or []
    for dept in departments:
        name = dept.get("name") if isinstance(dept, dict) else None
        if name:
            return name
    return None


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))
    for job in payload.get("jobs") or []:
        title = (job.get("title") or "").strip()
        url = job.get("absolute_url")
        location = ((job.get("location") or {}).get("name") or "").strip()
        description = _clean_description(job.get("content"))

        if not (title and url and location and description):
            continue

        item = {
            "url": url,
            "title": title,
            "company": "Honeycomb.io",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(job.get("id")),
        }

        if job.get("first_published"):
            item["posted_at"] = job["first_published"]
        if job.get("application_deadline"):
            item["expires_at"] = job["application_deadline"]

        department = _first_department(job)
        if department:
            item["department"] = department

        yield item
