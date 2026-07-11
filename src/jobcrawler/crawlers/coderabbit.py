
import json
import re
from html import unescape

SITE = "https://api.ashbyhq.com/posting-api/job-board/coderabbit?includeCompensation=true"
EXTRA_HOSTS = []

COMPANY = "CodeRabbit"

def _html_to_text(html):
    if not html:
        return None
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", html)
    text = re.sub(r"(?i)</\s*(p|li|h1|h2|h3|h4|div)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None

def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value

def _locations(job):
    primary = _clean(job.get("location"))
    seen = set()
    out = []
    candidates = [primary]
    for secondary in job.get("secondaryLocations") or []:
        if isinstance(secondary, dict):
            candidates.append(secondary.get("location"))
        else:
            candidates.append(secondary)
    for loc in candidates:
        loc = _clean(loc)
        if loc and loc not in seen:
            seen.add(loc)
            out.append(loc)
    return out

def _add_salary(job, item):
    comp = job.get("compensation") or {}
    salary_text = _clean(comp.get("compensationTierSummary") or comp.get("scrapeableCompensationSalarySummary"))
    if salary_text:
        item["salary"] = salary_text

    components = comp.get("summaryComponents") or []
    salary_components = []
    for component in components:
        if not isinstance(component, dict):
            continue
        if component.get("compensationType") != "Salary":
            continue
        if not component.get("currencyCode"):
            continue
        if component.get("minValue") is None and component.get("maxValue") is None:
            continue
        salary_components.append(component)

    currencies = set(component.get("currencyCode") for component in salary_components)
    if len(currencies) == 1 and salary_components:
        mins = [component.get("minValue") for component in salary_components if component.get("minValue") is not None]
        maxs = [component.get("maxValue") for component in salary_components if component.get("maxValue") is not None]
        if mins:
            item["salary_min"] = min(mins)
        if maxs:
            item["salary_max"] = max(maxs)
        item["salary_currency"] = list(currencies)[0]
        periods = [_clean(component.get("interval")) for component in salary_components if _clean(component.get("interval"))]
        if periods:
            item["salary_period"] = periods[0]

def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue

        description = _clean(job.get("descriptionPlain")) or _html_to_text(job.get("descriptionHtml"))
        locations = _locations(job)
        primary_location = _clean(job.get("location")) or (locations[0] if locations else None)

        item = {
            "url": _clean(job.get("jobUrl")) or SITE,
            "title": _clean(job.get("title")),
            "company": COMPANY,
            "location": primary_location,
            "description": description,
            "source": SITE,
            "external_id": _clean(job.get("id")),
            "department": _clean(job.get("department") or job.get("team")),
            "employment_type": _clean(job.get("employmentType")),
            "workplace_type": _clean(job.get("workplaceType")) or ("Remote" if job.get("isRemote") else None),
            "posted_at": _clean(job.get("publishedAt")),
            "apply_url": _clean(job.get("applyUrl")),
        }
        if locations:
            item["locations"] = locations
        _add_salary(job, item)

        yield {k: v for k, v in item.items() if v is not None}
