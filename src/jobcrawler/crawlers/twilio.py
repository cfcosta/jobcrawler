import html
import json
import re

SITE = 'https://boards-api.greenhouse.io/v1/boards/twilio/jobs?content=true'
EXTRA_HOSTS = []


def _response_text(response):
    if isinstance(response, str):
        return response
    if hasattr(response, "text"):
        return response.text
    return str(response)


def _clean_description(value):
    if value is None:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n", text)
    text = re.sub(r"(?i)</li\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\r", "\n", text)
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _department(job):
    departments = job.get("departments") or []
    names = []
    for dep in departments:
        if isinstance(dep, dict):
            name = dep.get("name")
            if name:
                names.append(str(name).strip())
    return ", ".join(n for n in names if n) or None


def _location(job):
    loc = job.get("location")
    if isinstance(loc, dict):
        name = loc.get("name")
        if name:
            return str(name).strip() or None
    if isinstance(loc, str):
        return loc.strip() or None
    return None


def crawl(ctx):
    raw = _response_text(ctx.fetch(SITE))
    data = json.loads(raw)
    for job in data.get("jobs", []):
        title = (job.get("title") or "").strip()
        url = (job.get("absolute_url") or "").strip()
        description = _clean_description(job.get("content"))
        location = _location(job)
        if not (title and url and description and location):
            continue

        item = {
            "url": url,
            "title": title,
            "company": (job.get("company_name") or "Twilio").strip(),
            "location": location,
            "description": description,
            "source": SITE,
        }

        external_id = job.get("id") or job.get("internal_job_id") or job.get("requisition_id")
        if external_id is not None:
            item["external_id"] = str(external_id)

        department = _department(job)
        if department:
            item["department"] = department

        posted_at = job.get("first_published")
        if posted_at:
            item["posted_at"] = posted_at

        expires_at = job.get("application_deadline")
        if expires_at:
            item["expires_at"] = expires_at

        apply_url = job.get("absolute_url")
        if apply_url:
            item["apply_url"] = apply_url

        yield item
