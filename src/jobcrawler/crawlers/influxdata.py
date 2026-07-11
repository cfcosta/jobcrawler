
import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/Influxdata?includeCompensation=true'
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
        loc = _clean(item.get("location") if isinstance(item, dict) else item)
        if loc and loc not in locations:
            locations.append(loc)
    return locations


def _salary_fields(job):
    comp = job.get("compensation") or {}
    fields = {}

    printed = _clean(comp.get("scrapeableCompensationSalarySummary")) or _clean(comp.get("compensationTierSummary"))
    if printed:
        fields["salary"] = printed

    for component in comp.get("summaryComponents") or []:
        if not isinstance(component, dict):
            continue
        if component.get("compensationType") != "Salary":
            continue
        currency = _clean(component.get("currencyCode"))
        min_value = component.get("minValue")
        max_value = component.get("maxValue")
        if currency and (min_value is not None or max_value is not None):
            fields["salary_currency"] = currency
            if min_value is not None:
                fields["salary_min"] = min_value
            if max_value is not None:
                fields["salary_max"] = max_value
            period = _clean(component.get("interval"))
            if period:
                fields["salary_period"] = period
            break

    return fields


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs") or []:
        if job.get("isListed") is False:
            continue

        title = _clean(job.get("title"))
        url = _clean(job.get("jobUrl"))
        description = _clean(job.get("descriptionPlain"))
        location = _clean(job.get("location"))
        if not title or not url or not description or not location:
            continue

        out = {
            "url": url,
            "title": title,
            "company": "InfluxData",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": _clean(job.get("id")),
            "department": _clean(job.get("department")) or _clean(job.get("team")),
            "employment_type": _clean(job.get("employmentType")),
            "workplace_type": _clean(job.get("workplaceType")),
            "posted_at": _clean(job.get("publishedAt")),
            "apply_url": _clean(job.get("applyUrl")),
        }

        locs = _locations(job)
        if locs:
            out["locations"] = locs

        out.update(_salary_fields(job))

        yield {k: v for k, v in out.items() if v is not None}
