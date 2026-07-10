
import json
import re
import html

SITE = "https://boards-api.greenhouse.io/v1/boards/bellroy/jobs?content=true"
EXTRA_HOSTS = []

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|ul|ol|section|article|blockquote)\s*>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    return text or None

def _names(items):
    values = []
    for item in items or []:
        if isinstance(item, dict):
            name = item.get("name")
            if name:
                values.append(name)
    return values

def _salary_fields(job):
    ranges = job.get("pay_input_ranges") or []
    if not ranges:
        return {}
    currencies = {r.get("currency_type") for r in ranges if r.get("currency_type")}
    if len(currencies) != 1:
        return {}
    mins = [r.get("min_cents") for r in ranges if r.get("min_cents") is not None]
    maxs = [r.get("max_cents") for r in ranges if r.get("max_cents") is not None]
    if not mins and not maxs:
        return {}
    out = {"salary_currency": next(iter(currencies))}
    if mins:
        out["salary_min"] = min(mins) / 100
    if maxs:
        out["salary_max"] = max(maxs) / 100
    period = None
    for r in ranges:
        period = r.get("unit")
        if period:
            break
    if period:
        out["salary_period"] = period
    return out

def crawl(ctx):
    url = SITE + "&pay_transparency=true"
    data = json.loads(ctx.fetch(url))
    for job in data.get("jobs", []):
        title = job.get("title")
        loc = (job.get("location") or {}).get("name")
        desc = _clean_html(job.get("content"))
        job_url = job.get("absolute_url")
        if not (title and loc and desc and job_url):
            continue

        item = {
            "url": job_url,
            "title": title,
            "company": "Bellroy",
            "location": loc,
            "description": desc,
            "source": SITE,
            "external_id": str(job.get("id")),
        }

        if job.get("requisition_id"):
            item["apply_url"] = job_url

        departments = _names(job.get("departments"))
        if departments:
            item["department"] = departments[0]

        offices = _names(job.get("offices"))
        if offices:
            item["locations"] = offices

        if job.get("first_published"):
            item["posted_at"] = job.get("first_published")
        if job.get("application_deadline"):
            item["expires_at"] = job.get("application_deadline")

        item.update(_salary_fields(job))
        yield item
