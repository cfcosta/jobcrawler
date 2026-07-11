
import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/formance?includeCompensation=true'
EXTRA_HOSTS = []

def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value

def _locations(job):
    locations = []
    primary = _clean(job.get("location"))
    if primary:
        locations.append(primary)
    for item in job.get("secondaryLocations") or []:
        loc = item.get("location") if isinstance(item, dict) else item
        loc = _clean(loc)
        if loc and loc not in locations:
            locations.append(loc)
    return locations

def _add_compensation(out, job):
    comp = job.get("compensationTierSummary") or job.get("compensation") or {}
    if not isinstance(comp, dict):
        return
    summary = _clean(comp.get("summary"))
    if summary:
        out["salary"] = summary
    components = comp.get("summaryComponents") or []
    if not isinstance(components, list):
        return

    min_value = max_value = currency = interval = None
    for part in components:
        if not isinstance(part, dict):
            continue
        if part.get("minValue") is not None:
            min_value = part.get("minValue")
        if part.get("maxValue") is not None:
            max_value = part.get("maxValue")
        if part.get("currencyCode"):
            currency = part.get("currencyCode")
        if part.get("interval"):
            interval = part.get("interval")
    if min_value is not None:
        out["salary_min"] = min_value
    if max_value is not None:
        out["salary_max"] = max_value
    if currency:
        out["salary_currency"] = currency
    if interval:
        out["salary_period"] = interval

def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue
        title = _clean(job.get("title"))
        description = _clean(job.get("descriptionPlain")) or _clean(job.get("description"))
        url = _clean(job.get("jobUrl"))
        location = _clean(job.get("location"))
        if not (title and description and url and location):
            continue

        out = {
            "url": url,
            "title": title,
            "company": "Formance",
            "location": location,
            "description": description,
            "source": SITE,
        }

        locs = _locations(job)
        if locs:
            out["locations"] = locs

        fields = {
            "employment_type": job.get("employmentType"),
            "workplace_type": job.get("workplaceType"),
            "department": job.get("department") or job.get("team"),
            "posted_at": job.get("publishedAt"),
            "external_id": job.get("id"),
            "apply_url": job.get("applyUrl"),
        }
        for key, value in fields.items():
            value = _clean(value)
            if value is not None:
                out[key] = value

        _add_compensation(out, job)
        yield out
