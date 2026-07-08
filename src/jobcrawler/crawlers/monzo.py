
import json
import html
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/monzo/jobs?content=true"
EXTRA_HOSTS = []


def _strip_html(value):
    if not value:
        return None

    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr|section|ul|ol)\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*(p|div|li|h[1-6]|tr|section|ul|ol)[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip() or None


def _department_name(job):
    names = []
    for item in job.get("departments") or []:
        if isinstance(item, dict):
            name = item.get("name")
            if name:
                names.append(str(name).strip())
    return ", ".join(name for name in names if name) or None


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        title = job.get("title")
        url = job.get("absolute_url")
        description = _strip_html(job.get("content"))
        location = (job.get("location") or {}).get("name")
        if not (title and url and description and location):
            continue

        item = {
            "url": url,
            "title": title,
            "company": job.get("company_name") or "Monzo",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(job.get("id")),
        }

        if job.get("first_published"):
            item["posted_at"] = job["first_published"]
        if job.get("application_deadline"):
            item["expires_at"] = job["application_deadline"]

        department = _department_name(job)
        if department:
            item["department"] = department

        yield item
