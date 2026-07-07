
import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/kraken.com?includeCompensation=true"
EXTRA_HOSTS = ["jobs.ashbyhq.com"]

def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value

def _location(job):
    locs = []
    primary = _clean(job.get("location"))
    if primary:
        locs.append(primary)
    for secondary in job.get("secondaryLocations") or []:
        name = _clean(secondary.get("location"))
        if name and name not in locs:
            locs.append(name)
    return "; ".join(locs) if locs else None

def _salary(compensation):
    if not isinstance(compensation, dict):
        return None
    components = compensation.get("summaryComponents") or []
    parts = []
    for comp in components:
        if not isinstance(comp, dict):
            continue
        label = _clean(comp.get("label"))
        currency = _clean(comp.get("currencyCode"))
        interval = _clean(comp.get("interval"))
        min_value = comp.get("minValue")
        max_value = comp.get("maxValue")
        if min_value is None and max_value is None:
            continue
        if min_value is not None and max_value is not None:
            amount = "{}-{}".format(min_value, max_value)
        elif min_value is not None:
            amount = "from {}".format(min_value)
        else:
            amount = "up to {}".format(max_value)
        text = " ".join(x for x in [label, currency, amount, interval] if x)
        if text:
            parts.append(text)
    if parts:
        return "; ".join(parts)
    summary = _clean(compensation.get("scrapeableCompensationSalarySummary"))
    if summary:
        return summary
    return _clean(compensation.get("compensationTierSummary"))

def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs") or []:
        if job.get("isListed") is False:
            continue

        title = _clean(job.get("title"))
        url = _clean(job.get("jobUrl"))
        description = _clean(job.get("descriptionPlain") or job.get("descriptionHtml"))
        location = _location(job)
        if not (title and url and description and location):
            continue

        item = {
            "url": url,
            "title": title,
            "company": "Kraken",
            "location": location,
            "description": description,
            "source": SITE,
        }

        field_map = {
            "employment_type": job.get("employmentType"),
            "workplace_type": job.get("workplaceType") or ("Remote" if job.get("isRemote") is True else None),
            "department": job.get("department") or job.get("team"),
            "posted_at": job.get("publishedAt"),
            "external_id": job.get("id"),
            "apply_url": job.get("applyUrl"),
        }
        salary = _salary(job.get("compensation"))
        if salary:
            field_map["salary"] = salary

        for key, value in field_map.items():
            value = _clean(value)
            if value is not None:
                item[key] = value

        yield item
