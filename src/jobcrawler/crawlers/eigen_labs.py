
import json
import re
from html import unescape

SITE = "https://api.ashbyhq.com/posting-api/job-board/eigen-labs?includeCompensation=true"
EXTRA_HOSTS = []

def _clean_html(value):
    if not value:
        return None
    text = re.sub(r"<[^>]+>", " ", unescape(value))
    text = re.sub(r"\s+", " ", text).strip()
    return text or None

def _job_kwargs(job):
    description = job.get("descriptionPlain") or _clean_html(job.get("descriptionHtml"))
    location = job.get("location")
    if not location and job.get("address"):
        location = (((job.get("address") or {}).get("postalAddress") or {}).get("addressLocality"))

    item = {
        "url": job.get("jobUrl"),
        "title": job.get("title"),
        "company": "Eigen Labs",
        "location": location,
        "description": description,
        "source": SITE,
        "external_id": job.get("id"),
        "apply_url": job.get("applyUrl"),
        "employment_type": job.get("employmentType"),
        "workplace_type": job.get("workplaceType") or ("Remote" if job.get("isRemote") else None),
        "department": job.get("department") or job.get("team"),
        "posted_at": job.get("publishedAt"),
    }

    comp = job.get("compensation") or {}
    summary = comp.get("summaryComponents") or []
    salaries = []
    for component in summary:
        if not isinstance(component, dict):
            continue
        min_value = component.get("minValue")
        max_value = component.get("maxValue")
        currency = component.get("currencyCode")
        interval = component.get("interval")
        if min_value is None and max_value is None:
            continue
        salary = {}
        if min_value is not None:
            salary["min"] = min_value
        if max_value is not None:
            salary["max"] = max_value
        if currency:
            salary["currency"] = currency
        if interval:
            salary["interval"] = interval
        salaries.append(salary)
    if salaries:
        item["salary"] = salaries[0] if len(salaries) == 1 else salaries

    return {k: v for k, v in item.items() if v not in (None, "")}

def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue
        item = _job_kwargs(job)
        if item.get("url") and item.get("title") and item.get("description"):
            yield item
