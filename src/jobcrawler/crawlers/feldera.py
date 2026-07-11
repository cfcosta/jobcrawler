
import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/feldera?includeCompensation=true"
EXTRA_HOSTS = []


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _compensation_fields(compensation):
    fields = {}
    if not isinstance(compensation, dict):
        return fields

    text = _clean(compensation.get("scrapeableCompensationSalarySummary") or compensation.get("compensationTierSummary"))
    if text:
        fields["salary"] = text

    components = compensation.get("summaryComponents") or []
    currencies = {c.get("currencyCode") for c in components if c.get("currencyCode")}
    if len(currencies) == 1:
        mins = [c.get("minValue") for c in components if isinstance(c.get("minValue"), (int, float))]
        maxs = [c.get("maxValue") for c in components if isinstance(c.get("maxValue"), (int, float))]
        intervals = [c.get("interval") for c in components if c.get("interval")]
        if mins:
            fields["salary_min"] = min(mins)
        if maxs:
            fields["salary_max"] = max(maxs)
        fields["salary_currency"] = next(iter(currencies))
        if intervals:
            fields["salary_period"] = intervals[0]
    return fields


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

        locations = []
        if location:
            locations.append(location)
        for item in job.get("secondaryLocations") or []:
            if isinstance(item, dict):
                loc = _clean(item.get("location") or item.get("name"))
            else:
                loc = _clean(item)
            if loc and loc not in locations:
                locations.append(loc)

        record = {
            "url": url,
            "title": title,
            "company": "Feldera",
            "location": location,
            "locations": locations,
            "description": description,
            "source": SITE,
            "external_id": _clean(job.get("id")),
            "department": _clean(job.get("department") or job.get("team")),
            "employment_type": _clean(job.get("employmentType")),
            "workplace_type": _clean(job.get("workplaceType") or ("Remote" if job.get("isRemote") else None)),
            "posted_at": _clean(job.get("publishedAt")),
            "apply_url": _clean(job.get("applyUrl")),
        }
        record.update(_compensation_fields(job.get("compensation")))

        yield {key: value for key, value in record.items() if value is not None}
