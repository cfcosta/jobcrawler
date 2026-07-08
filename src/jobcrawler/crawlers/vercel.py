
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/vercel/jobs?content=true"
EXTRA_HOSTS = []


def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _department(job):
    departments = job.get("departments") or []
    names = []
    for item in departments:
        if isinstance(item, dict):
            name = item.get("name")
            if name:
                names.append(str(name))
    if names:
        return ", ".join(names)
    return None


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        description = _clean_html(job.get("content"))
        title = job.get("title")
        url = job.get("absolute_url")
        location = (job.get("location") or {}).get("name")
        if not (title and url and location and description):
            continue

        out = {
            "url": url,
            "title": title,
            "company": job.get("company_name") or "Vercel",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(job.get("id")),
            "apply_url": url,
        }

        department = _department(job)
        if department:
            out["department"] = department

        if job.get("first_published"):
            out["posted_at"] = job.get("first_published")
        if job.get("application_deadline"):
            out["expires_at"] = job.get("application_deadline")

        yield out
