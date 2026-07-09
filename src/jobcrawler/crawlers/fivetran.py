
import json
import html
import re

SITE = 'https://boards-api.greenhouse.io/v1/boards/fivetran/jobs?content=true'
EXTRA_HOSTS = []

_BLOCK_TAG_RE = re.compile(r"</?(?:p|div|section|article|br|li|h[1-6]|tr|ul|ol|table)[^>]*>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_RE = re.compile(r"\n\s*\n+")

def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"<(?:script|style)[\s\S]*?</(?:script|style)>", " ", text, flags=re.I)
    text = _BLOCK_TAG_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _SPACE_RE.sub(" ", text)
    text = _BLANK_RE.sub("\n\n", text)
    text = text.strip()
    return text or None

def _location(job):
    loc = job.get("location") or {}
    if isinstance(loc, dict):
        return loc.get("name") or None
    return None

def _department(job):
    deps = job.get("departments") or []
    names = []
    for dep in deps:
        if isinstance(dep, dict) and dep.get("name"):
            names.append(dep["name"])
    return ", ".join(names) if names else None

def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        title = job.get("title")
        url = job.get("absolute_url")
        location = _location(job)
        description = _clean_html(job.get("content"))
        if not (title and url and location and description):
            continue

        item = {
            "url": url,
            "title": title,
            "company": "Fivetran",
            "location": location,
            "description": description,
            "source": SITE,
        }

        external_id = job.get("id") or job.get("internal_job_id")
        if external_id is not None:
            item["external_id"] = str(external_id)

        posted_at = job.get("updated_at")
        if posted_at:
            item["posted_at"] = posted_at

        department = _department(job)
        if department:
            item["department"] = department

        apply_url = job.get("absolute_url")
        if apply_url:
            item["apply_url"] = apply_url

        yield item
