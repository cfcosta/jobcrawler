
import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/paddle?includeCompensation=true'
EXTRA_HOSTS = []

def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value

def _locations(job):
    seen = set()
    values = []
    for loc in [job.get("location")] + [x.get("location") for x in (job.get("secondaryLocations") or []) if isinstance(x, dict)]:
        loc = _clean(loc)
        if loc and loc not in seen:
            seen.add(loc)
            values.append(loc)
    return values

def _add_if(job_out, key, value):
    value = _clean(value)
    if value is not None:
        job_out[key] = value

def crawl(ctx):
    raw = ctx.fetch(SITE)
    data = json.loads(raw)
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue

        description = _clean(job.get("descriptionPlain"))
        title = _clean(job.get("title"))
        url = _clean(job.get("jobUrl"))
        location = _clean(job.get("location"))
        if not (title and url and location and description):
            continue

        out = {
            "url": url,
            "title": title,
            "company": "Paddle",
            "location": location,
            "description": description,
            "source": SITE,
        }

        locs = _locations(job)
        if locs:
            out["locations"] = locs

        _add_if(out, "external_id", job.get("id"))
        _add_if(out, "apply_url", job.get("applyUrl"))
        _add_if(out, "department", job.get("department"))
        _add_if(out, "employment_type", job.get("employmentType"))
        _add_if(out, "workplace_type", job.get("workplaceType"))
        _add_if(out, "posted_at", job.get("publishedAt"))

        comp = job.get("compensation") or {}
        summary = _clean(comp.get("scrapeableCompensationSalarySummary") or comp.get("compensationTierSummary"))
        if summary:
            out["salary"] = summary

        components = comp.get("summaryComponents") or []
        if len(components) == 1 and isinstance(components[0], dict):
            c = components[0]
            currency = _clean(c.get("currencyCode"))
            min_value = c.get("minValue")
            max_value = c.get("maxValue")
            if currency and (min_value is not None or max_value is not None):
                if min_value is not None:
                    out["salary_min"] = min_value
                if max_value is not None:
                    out["salary_max"] = max_value
                out["salary_currency"] = currency
                _add_if(out, "salary_period", c.get("interval"))

        yield out
