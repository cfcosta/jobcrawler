
import html
import json
import re

SITE = 'https://boards-api.greenhouse.io/v1/boards/affirm/jobs?content=true'
EXTRA_HOSTS = []


def _strip_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|ul|ol|section|tr)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip() or None


def _metadata_value(job, name):
    for item in job.get("metadata") or []:
        if item.get("name") == name and item.get("value") not in (None, ""):
            return item.get("value")
    return None


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs") or []:
        title = (job.get("title") or "").strip()
        url = (job.get("absolute_url") or "").strip()
        location = ((job.get("location") or {}).get("name") or "").strip()
        description = _strip_html(job.get("content"))

        if not (title and url and location and description):
            continue

        item = {
            "url": url,
            "title": title,
            "company": "Affirm",
            "location": location,
            "description": description,
            "source": SITE,
        }

        external_id = job.get("id") or job.get("internal_job_id")
        if external_id is not None:
            item["external_id"] = str(external_id)

        department = _metadata_value(job, "External Department")
        if not department:
            departments = job.get("departments") or []
            if departments:
                department = departments[0].get("name")
        if department:
            item["department"] = str(department).strip()

        apply_url = job.get("absolute_url")
        if apply_url:
            item["apply_url"] = apply_url

        yield item
