
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/pingcap/jobs?content=true"
EXTRA_HOSTS = []

_TAG_RE = re.compile(r"<[^>]+>")


def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|section|tr)\s*>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _names(items):
    out = []
    for item in items or []:
        if isinstance(item, dict):
            name = item.get("name")
            if name:
                out.append(str(name))
    return out


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs") or []:
        title = job.get("title")
        location_obj = job.get("location") or {}
        location = location_obj.get("name") if isinstance(location_obj, dict) else None
        description = _clean_html(job.get("content"))
        url = job.get("absolute_url")
        if not (title and location and description and url):
            continue

        offices = _names(job.get("offices"))
        departments = _names(job.get("departments"))

        item = {
            "source": SITE,
            "url": url,
            "title": title,
            "company": job.get("company_name") or "TiDB",
            "location": location,
            "description": description,
            "external_id": str(job.get("id")) if job.get("id") is not None else None,
            "apply_url": url,
            "posted_at": job.get("first_published"),
        }

        if offices:
            item["locations"] = offices
        if departments:
            item["department"] = departments[0]
        if job.get("application_deadline"):
            item["expires_at"] = job.get("application_deadline")

        yield {k: v for k, v in item.items() if v is not None}
