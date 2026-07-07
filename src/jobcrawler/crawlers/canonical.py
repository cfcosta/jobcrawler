
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/canonical/jobs?content=true"


def _html_to_text(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr|ul|ol)\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*(p|div|li|h[1-6]|tr|ul|ol)[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return text or None


def _metadata_value(job, name):
    for item in job.get("metadata") or []:
        if item.get("name") == name:
            value = item.get("value")
            if value in (None, "", []):
                return None
            if isinstance(value, list):
                values = [str(v) for v in value if v not in (None, "")]
                return ", ".join(values) if values else None
            return str(value)
    return None


def _department(job):
    departments = job.get("departments") or []
    names = [d.get("name") for d in departments if d.get("name")]
    if names:
        return ", ".join(names)
    return _metadata_value(job, "External Departments (website)")


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs") or []:
        title = job.get("title")
        url = job.get("absolute_url")
        location = (job.get("location") or {}).get("name")
        description = _html_to_text(job.get("content"))
        if not (title and url and location and description):
            continue

        item = {
            "url": url,
            "title": title,
            "company": job.get("company_name") or "Canonical",
            "location": location,
            "description": description,
            "source": SITE,
        }

        optional = {
            "department": _department(job),
            "employment_type": _metadata_value(job, "Employment Length"),
            "posted_at": job.get("first_published"),
            "external_id": str(job.get("id")) if job.get("id") is not None else None,
            "apply_url": url,
        }
        for key, value in optional.items():
            if value not in (None, "", []):
                item[key] = value

        yield item
