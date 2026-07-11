
import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/snowflake?includeCompensation=true"
EXTRA_HOSTS = []

COMPANY = "Snowflake"

def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value

def _locations(job):
    values = []
    primary = _clean(job.get("location"))
    if primary:
        values.append(primary)
    for item in job.get("secondaryLocations") or []:
        loc = _clean(item.get("location") if isinstance(item, dict) else item)
        if loc and loc not in values:
            values.append(loc)
    return values

def _add_compensation(out, job):
    comp = job.get("compensation") or {}
    text = _clean(comp.get("scrapeableCompensationSalarySummary") or comp.get("compensationTierSummary"))
    if text:
        out["salary"] = text

    components = comp.get("summaryComponents") or []
    usable = []
    for c in components:
        if not isinstance(c, dict):
            continue
        currency = _clean(c.get("currencyCode"))
        if c.get("minValue") is None and c.get("maxValue") is None:
            continue
        if not currency:
            continue
        usable.append(c)

    currencies = {c.get("currencyCode") for c in usable}
    if len(currencies) == 1 and usable:
        mins = [c.get("minValue") for c in usable if c.get("minValue") is not None]
        maxs = [c.get("maxValue") for c in usable if c.get("maxValue") is not None]
        if mins:
            out["salary_min"] = min(mins)
        if maxs:
            out["salary_max"] = max(maxs)
        out["salary_currency"] = usable[0].get("currencyCode")
        period = _clean(usable[0].get("interval"))
        if period:
            out["salary_period"] = period

def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue

        title = _clean(job.get("title"))
        description = _clean(job.get("descriptionPlain"))
        url = _clean(job.get("jobUrl"))
        location = _clean(job.get("location"))
        if not (title and description and url and location):
            continue

        out = {
            "url": url,
            "title": title,
            "company": COMPANY,
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": _clean(job.get("id")),
        }

        locs = _locations(job)
        if locs:
            out["locations"] = locs

        apply_url = _clean(job.get("applyUrl"))
        if apply_url:
            out["apply_url"] = apply_url

        employment_type = _clean(job.get("employmentType"))
        if employment_type:
            out["employment_type"] = employment_type

        workplace_type = _clean(job.get("workplaceType"))
        if workplace_type:
            out["workplace_type"] = workplace_type
        elif job.get("isRemote") is True:
            out["workplace_type"] = "Remote"

        department = _clean(job.get("department") or job.get("team"))
        if department:
            out["department"] = department

        posted_at = _clean(job.get("publishedAt"))
        if posted_at:
            out["posted_at"] = posted_at

        _add_compensation(out, job)

        yield out
