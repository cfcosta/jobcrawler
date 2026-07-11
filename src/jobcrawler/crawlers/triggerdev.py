
SITE = 'https://api.ashbyhq.com/posting-api/job-board/triggerdev?includeCompensation=true'
EXTRA_HOSTS = []

def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value

def _secondary_location_name(item):
    if isinstance(item, str):
        return _clean(item)
    if isinstance(item, dict):
        return _clean(item.get("location") or item.get("name"))
    return None

def _apply_compensation(fields, compensation):
    if not isinstance(compensation, dict):
        return

    summary = _clean(
        compensation.get("scrapeableCompensationSalarySummary")
        or compensation.get("compensationTierSummary")
    )
    if summary:
        fields["salary"] = summary

    components = compensation.get("summaryComponents") or []
    structured = []
    for component in components:
        if not isinstance(component, dict):
            continue
        min_value = component.get("minValue")
        max_value = component.get("maxValue")
        currency = _clean(component.get("currencyCode"))
        interval = _clean(component.get("interval"))
        if currency and (min_value is not None or max_value is not None):
            structured.append((min_value, max_value, currency, interval))

    currencies = {item[2] for item in structured}
    if len(currencies) == 1 and structured:
        mins = [item[0] for item in structured if item[0] is not None]
        maxs = [item[1] for item in structured if item[1] is not None]
        periods = [item[3] for item in structured if item[3]]
        if mins:
            fields["salary_min"] = min(mins)
        if maxs:
            fields["salary_max"] = max(maxs)
        fields["salary_currency"] = structured[0][2]
        if periods:
            fields["salary_period"] = periods[0]

def crawl(ctx):
    import json

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
        for secondary in job.get("secondaryLocations") or []:
            name = _secondary_location_name(secondary)
            if name and name not in locations:
                locations.append(name)

        fields = {
            "url": url,
            "title": title,
            "company": "Trigger.dev",
            "location": location,
            "locations": locations,
            "description": description,
            "source": SITE,
        }

        mappings = {
            "employmentType": "employment_type",
            "workplaceType": "workplace_type",
            "department": "department",
            "publishedAt": "posted_at",
            "id": "external_id",
            "applyUrl": "apply_url",
        }
        for src, dest in mappings.items():
            value = _clean(job.get(src))
            if value is not None:
                fields[dest] = value

        _apply_compensation(fields, job.get("compensation"))
        yield fields
