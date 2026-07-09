
import json
import re
import html

SITE = 'https://boards-api.greenhouse.io/v1/boards/aiven36/jobs?content=true'
EXTRA_HOSTS = []

def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value

def _text_from_greenhouse_content(value):
    text = html.unescape(value or "")
    text = re.sub(r"<(br|/p|/li|/div|/h[1-6])[^>]*>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()

def crawl(ctx):
    payload = ctx.fetch(SITE)
    data = json.loads(payload)
    for job in data.get("jobs", []):
        url = _clean(job.get("absolute_url"))
        item = {
            "url": url,
            "title": _clean(job.get("title")),
            "company": "Aiven",
            "location": _clean((job.get("location") or {}).get("name")),
            "description": _text_from_greenhouse_content(job.get("content")),
            "source": SITE,
            "external_id": str(job.get("id")) if job.get("id") is not None else None,
            "posted_at": _clean(job.get("first_published")),
            "expires_at": _clean(job.get("application_deadline")),
            "apply_url": url,
        }

        departments = job.get("departments") or []
        names = []
        for department in departments:
            if isinstance(department, dict):
                name = _clean(department.get("name"))
                if name:
                    names.append(name)
        if names:
            item["department"] = ", ".join(names)

        yield {key: value for key, value in item.items() if value is not None}
