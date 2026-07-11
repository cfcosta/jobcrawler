
import json
import re
from html import unescape

SITE = 'https://api.ashbyhq.com/posting-api/job-board/griffin?includeCompensation=true'
EXTRA_HOSTS = []

def _clean_html(value):
    if not value:
        return None
    text = re.sub(r"(?is)<(script|style).*?>.*?</\\1>", " ", value)
    text = re.sub(r"(?i)<br\\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip() or None

def _set(job, key, value):
    if value is not None and value != "":
        job[key] = value

def _locations(job):
    locations = []
    primary = job.get("location")
    if primary:
        locations.append(primary)
    for item in job.get("secondaryLocations") or []:
        loc = item.get("location") if isinstance(item, dict) else item
        if loc and loc not in locations:
            locations.append(loc)
    return locations

def _apply_compensation(out, comp):
    if not isinstance(comp, dict):
        return

    printed = comp.get("scrapeableCompensationSalarySummary") or comp.get("compensationTierSummary")
    _set(out, "salary", printed)

    components = comp.get("summaryComponents") or []
    currencies = set()
    mins = []
    maxs = []
    periods = []
    for c in components:
        if not isinstance(c, dict):
            continue
        currency = c.get("currencyCode") or c.get("currency")
        min_value = c.get("minValue")
        max_value = c.get("maxValue")
        interval = c.get("interval") or c.get("period")
        if currency:
            currencies.add(currency)
        if min_value is not None:
            mins.append(min_value)
        if max_value is not None:
            maxs.append(max_value)
        if interval:
            periods.append(interval)

    if len(currencies) == 1:
        _set(out, "salary_currency", next(iter(currencies)))
        if mins:
            _set(out, "salary_min", min(mins))
        if maxs:
            _set(out, "salary_max", max(maxs))
        if periods:
            _set(out, "salary_period", periods[0])

def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for item in data.get("jobs") or []:
        description = item.get("descriptionPlain") or _clean_html(item.get("descriptionHtml"))
        locs = _locations(item)
        out = {
            "url": item.get("jobUrl") or SITE,
            "title": item.get("title"),
            "company": "Griffin",
            "location": item.get("location") or (locs[0] if locs else None),
            "description": description,
            "source": SITE,
        }
        if len(locs) > 1:
            out["locations"] = locs
        _set(out, "external_id", item.get("id"))
        _set(out, "apply_url", item.get("applyUrl"))
        _set(out, "department", item.get("department") or item.get("team"))
        _set(out, "employment_type", item.get("employmentType"))
        _set(out, "workplace_type", item.get("workplaceType") or ("Remote" if item.get("isRemote") else None))
        _set(out, "posted_at", item.get("publishedAt"))
        _apply_compensation(out, item.get("compensation"))
        yield out
