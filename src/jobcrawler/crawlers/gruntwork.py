import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/Gruntwork?includeCompensation=true'
EXTRA_HOSTS = []


def _set(record, key, value):
    if value is not None and value != "":
        record[key] = value


def crawl(ctx):
    response = ctx.fetch(SITE)
    raw = response.text if hasattr(response, "text") else response
    data = json.loads(raw) if isinstance(raw, str) else raw

    for item in data.get("jobs", []):
        if item.get("isListed") is False:
            continue

        record = {
            "url": item.get("jobUrl"),
            "title": item.get("title"),
            "company": "Gruntwork",
            "location": item.get("location"),
            "description": item.get("descriptionPlain"),
            "source": SITE,
        }

        locations = []
        if item.get("location"):
            locations.append(item["location"])
        for secondary in item.get("secondaryLocations") or []:
            secondary_location = secondary.get("location")
            if secondary_location:
                locations.append(secondary_location)
        if locations:
            record["locations"] = locations

        _set(record, "employment_type", item.get("employmentType"))
        _set(record, "workplace_type", item.get("workplaceType"))
        _set(record, "department", item.get("department"))
        _set(record, "posted_at", item.get("publishedAt"))
        _set(record, "external_id", item.get("id"))
        _set(record, "apply_url", item.get("applyUrl"))

        compensation = item.get("compensation") or {}
        _set(record, "salary", compensation.get("compensationTierSummary"))

        salary_components = [
            component
            for component in compensation.get("summaryComponents") or []
            if component.get("compensationType") == "Salary"
        ]
        if len(salary_components) == 1:
            salary_component = salary_components[0]
            _set(record, "salary_min", salary_component.get("minValue"))
            _set(record, "salary_max", salary_component.get("maxValue"))
            _set(record, "salary_currency", salary_component.get("currencyCode"))
            _set(record, "salary_period", salary_component.get("interval"))

        yield record
