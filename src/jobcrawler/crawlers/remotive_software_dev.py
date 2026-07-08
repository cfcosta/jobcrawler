
import html
import json
import re

SITE = "https://remotive.com/api/remote-jobs?category=software-dev"
EXTRA_HOSTS = []

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

def _clean_description(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr)\s*>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    return text or None

def _clean_salary(value):
    if value is None:
        return None
    value = str(value).strip()
    if not value or value.lower() in {"n/a", "na", "not specified", "unspecified"}:
        return None
    return value

def _present(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value

def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for item in data.get("jobs") or []:
        job = {
            "url": _present(item.get("url")),
            "title": _present(item.get("title")),
            "company": _present(item.get("company_name")),
            "location": _present(item.get("candidate_required_location")),
            "description": _clean_description(item.get("description")),
            "source": SITE,
            "employment_type": _present(item.get("job_type")),
            "department": _present(item.get("category")),
            "posted_at": _present(item.get("publication_date")),
            "external_id": str(item.get("id")) if item.get("id") is not None else None,
            "apply_url": _present(item.get("url")),
            "salary": _clean_salary(item.get("salary")),
        }
        yield {key: value for key, value in job.items() if value is not None}
