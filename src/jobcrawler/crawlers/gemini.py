
import json
import re
import html

SITE = 'https://boards-api.greenhouse.io/v1/boards/gemini/jobs?content=true'
EXTRA_HOSTS = []

COMPANY = "Gemini"

def _clean_text(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|ul|ol|h[1-6]|tr)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n+ *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return text or None

def _first_text(value):
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return None

def _department(job):
    departments = job.get("departments")
    if isinstance(departments, list) and departments:
        names = []
        for item in departments:
            if isinstance(item, dict):
                name = _first_text(item.get("name"))
                if name:
                    names.append(name)
        if names:
            return ", ".join(names)
    return None

def crawl(ctx):
    raw = ctx.fetch(SITE)
    data = json.loads(raw)
    jobs = data.get("jobs", data if isinstance(data, list) else [])
    for job in jobs:
        if not isinstance(job, dict):
            continue

        title = _first_text(job.get("title"))
        url = _first_text(job.get("absolute_url"))
        location_obj = job.get("location") or {}
        location = _first_text(location_obj.get("name")) if isinstance(location_obj, dict) else _first_text(location_obj)
        description = _clean_text(job.get("content") or job.get("description"))

        if not (title and url and location and description):
            continue

        item = {
            "url": url,
            "title": title,
            "company": COMPANY,
            "location": location,
            "description": description,
            "source": SITE,
        }

        external_id = job.get("id") or job.get("internal_job_id")
        if external_id is not None:
            item["external_id"] = str(external_id)

        department = _department(job)
        if department:
            item["department"] = department

        yield item
