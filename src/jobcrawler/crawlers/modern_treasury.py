
import json
import re
from html import unescape

SITE = "https://api.ashbyhq.com/posting-api/job-board/moderntreasury?includeCompensation=true"
EXTRA_HOSTS = []

COMPANY = "Modern Treasury"

def _clean_html(value):
    if not value:
        return None
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None

def _location(job):
    locations = []
    primary = job.get("location")
    if primary:
        locations.append(primary)
    for item in job.get("secondaryLocations") or []:
        loc = item.get("location") if isinstance(item, dict) else None
        if loc and loc not in locations:
            locations.append(loc)
    return ", ".join(locations) if locations else None

def _salary(job):
    comp = job.get("compensation") or {}
    components = comp.get("summaryComponents") or []
    for component in components:
        if not isinstance(component, dict):
            continue
        if component.get("minValue") is None and component.get("maxValue") is None:
            continue
        if not component.get("currencyCode"):
            continue
        return {
            "salary_min": component.get("minValue"),
            "salary_max": component.get("maxValue"),
            "salary_currency": component.get("currencyCode"),
            "salary_interval": component.get("interval"),
        }
    return {}

def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs") or []:
        if job.get("isListed") is False:
            continue
        description = job.get("descriptionPlain") or _clean_html(job.get("descriptionHtml"))
        item = {
            "url": job.get("jobUrl") or SITE,
            "title": job.get("title"),
            "company": COMPANY,
            "location": _location(job),
            "description": description,
            "source": SITE,
            "external_id": job.get("id"),
            "apply_url": job.get("applyUrl"),
            "department": job.get("department") or job.get("team"),
            "employment_type": job.get("employmentType"),
            "workplace_type": job.get("workplaceType") or ("Remote" if job.get("isRemote") else None),
            "posted_at": job.get("publishedAt"),
        }
        item.update(_salary(job))
        yield {k: v for k, v in item.items() if v not in (None, "")}
