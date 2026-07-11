
import json
import re

SITE = "https://api.ashbyhq.com/posting-api/job-board/prefect?includeCompensation=true"
EXTRA_HOSTS = []


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _html_to_text(value):
    if not value:
        return None
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", value)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|ul|ol)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return _clean(text)


def _locations(job):
    values = []
    primary = _clean(job.get("location"))
    if primary:
        values.append(primary)
    for item in job.get("secondaryLocations") or []:
        loc = item.get("location") if isinstance(item, dict) else item
        loc = _clean(loc)
        if loc and loc not in values:
            values.append(loc)
    return values


def _salary_fields(job):
    comp = job.get("compensation") or {}
    out = {}
    if not isinstance(comp, dict):
        return out

    printed = _clean(comp.get("compensationTierSummary")) or _clean(comp.get("scrapeableCompensationSalarySummary"))
    if printed:
        out["salary"] = printed

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
    intervals = set(component.get("interval") for component in salary_components if component.get("interval"))

    if salary_components and len(currencies) == 1:
        mins = [component.get("minValue") for component in salary_components if component.get("minValue") is not None]
        maxs = [component.get("maxValue") for component in salary_components if component.get("maxValue") is not None]
        if mins:
            out["salary_min"] = min(mins)
        if maxs:
            out["salary_max"] = max(maxs)
        out["salary_currency"] = list(currencies)[0]
        if len(intervals) == 1:
            out["salary_period"] = list(intervals)[0]

    return out


def _job_payload(job):
    description = _clean(job.get("descriptionPlain")) or _html_to_text(job.get("descriptionHtml"))
    locs = _locations(job)

    payload = {
        "url": _clean(job.get("jobUrl")),
        "title": _clean(job.get("title")),
        "company": "Prefect",
        "location": _clean(job.get("location")) or (locs[0] if locs else None),
        "description": description,
        "source": SITE,
        "external_id": _clean(job.get("id")),
        "department": _clean(job.get("department")) or _clean(job.get("team")),
        "employment_type": _clean(job.get("employmentType")),
        "workplace_type": _clean(job.get("workplaceType")) or ("Remote" if job.get("isRemote") is True else None),
        "posted_at": _clean(job.get("publishedAt")),
        "apply_url": _clean(job.get("applyUrl")),
    }

    if locs:
        payload["locations"] = locs

    payload.update(_salary_fields(job))
    return dict((key, value) for key, value in payload.items() if value is not None and value != "")


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue
        payload = _job_payload(job)
        if payload.get("url") and payload.get("title") and payload.get("description"):
            yield payload
