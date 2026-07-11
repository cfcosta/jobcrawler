
import json
import re
from html import unescape

SITE = "https://api.ashbyhq.com/posting-api/job-board/kong?includeCompensation=true"
EXTRA_HOSTS = []


def _html_to_text(html):
    if not html:
        return None
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", html)
    text = re.sub(r"(?i)</\s*(p|div|li|tr|h[1-6])\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    text = text.strip()
    return text or None


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _salary_fields(job):
    comp = job.get("compensation") or {}
    out = {}
    salary_text = _clean(comp.get("scrapeableCompensationSalarySummary")) or _clean(comp.get("compensationTierSummary"))
    if salary_text:
        out["salary"] = salary_text

    salary_components = [
        c for c in (comp.get("summaryComponents") or [])
        if c.get("compensationType") == "Salary"
        and c.get("currencyCode")
        and (c.get("minValue") is not None or c.get("maxValue") is not None)
    ]
    currencies = {c.get("currencyCode") for c in salary_components if c.get("currencyCode")}
    if len(currencies) == 1 and salary_components:
        c = salary_components[0]
        if c.get("minValue") is not None:
            out["salary_min"] = c.get("minValue")
        if c.get("maxValue") is not None:
            out["salary_max"] = c.get("maxValue")
        out["salary_currency"] = c.get("currencyCode")
        period = _clean(c.get("interval"))
        if period:
            out["salary_period"] = period
    return out


def _make_job(raw):
    description = _clean(raw.get("descriptionPlain")) or _html_to_text(raw.get("descriptionHtml"))
    primary_location = _clean(raw.get("location"))
    item = {
        "url": _clean(raw.get("jobUrl")),
        "title": _clean(raw.get("title")),
        "company": "Kong",
        "location": primary_location,
        "description": description,
        "source": SITE,
    }

    optional = {
        "external_id": _clean(raw.get("id")),
        "apply_url": _clean(raw.get("applyUrl")),
        "employment_type": _clean(raw.get("employmentType")),
        "workplace_type": _clean(raw.get("workplaceType")) or ("Remote" if raw.get("isRemote") else None),
        "department": _clean(raw.get("team")) or _clean(raw.get("department")),
        "posted_at": _clean(raw.get("publishedAt")),
    }
    for key, value in optional.items():
        if value is not None:
            item[key] = value

    locations = []
    if primary_location:
        locations.append(primary_location)
    for secondary in raw.get("secondaryLocations") or []:
        loc = _clean((secondary or {}).get("location"))
        if loc and loc not in locations:
            locations.append(loc)
    if len(locations) > 1:
        item["locations"] = locations

    item.update(_salary_fields(raw))
    return {k: v for k, v in item.items() if v is not None and v != ""}


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for raw in data.get("jobs", []):
        if raw.get("isListed") is False:
            continue
        job = _make_job(raw)
        if job.get("url") and job.get("title") and job.get("location") and job.get("description"):
            yield job
