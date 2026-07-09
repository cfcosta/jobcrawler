import json
import re
from html import unescape

SITE = 'https://api.ashbyhq.com/posting-api/job-board/column?includeCompensation=true'
EXTRA_HOSTS = []

TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\n{3,}")

def _clean_html(value):
    if not value:
        return None
    text = TAG_RE.sub(" ", unescape(value))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return SPACE_RE.sub("\n\n", text).strip() or None

def _put(job, key, value):
    if value is not None and value != "":
        job[key] = value

def _add_compensation(job, compensation):
    if not isinstance(compensation, dict):
        return
    components = compensation.get("summaryComponents") or []
    if not isinstance(components, list):
        return
    for component in components:
        if not isinstance(component, dict):
            continue
        min_value = component.get("minValue")
        max_value = component.get("maxValue")
        currency = component.get("currencyCode")
        interval = component.get("interval")
        if min_value is not None:
            job["salary_min"] = min_value
        if max_value is not None:
            job["salary_max"] = max_value
        _put(job, "salary_currency", currency)
        _put(job, "salary_period", interval)
        if min_value is not None or max_value is not None or currency or interval:
            return

def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for item in data.get("jobs", []):
        if item.get("isListed") is False:
            continue

        description = item.get("descriptionPlain") or _clean_html(item.get("descriptionHtml"))
        job = {
            "url": item.get("jobUrl") or SITE,
            "title": item.get("title"),
            "company": "Column",
            "location": item.get("location"),
            "description": description,
            "source": SITE,
        }

        _put(job, "employment_type", item.get("employmentType"))
        _put(job, "workplace_type", item.get("workplaceType") or ("Remote" if item.get("isRemote") is True else None))
        _put(job, "department", item.get("department") or item.get("team"))
        _put(job, "posted_at", item.get("publishedAt"))
        _put(job, "external_id", item.get("id"))
        _put(job, "apply_url", item.get("applyUrl"))
        _add_compensation(job, item.get("compensation"))

        yield job
