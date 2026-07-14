import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/nightfall-ai?includeCompensation=true"
EXTRA_HOSTS = []


def _location_name(value):
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        return value.get("location") or value.get("name")
    return None


def crawl(ctx):
    response = ctx.fetch(SITE)
    try:
        payload = response.json()
    except AttributeError:
        payload = json.loads(response)

    for item in payload.get("jobs", []):
        if item.get("isListed") is False:
            continue

        location = item.get("location")
        locations = []
        if location:
            locations.append(location)
        for secondary in item.get("secondaryLocations") or []:
            name = _location_name(secondary)
            if name and name not in locations:
                locations.append(name)

        fields = {
            "source": SITE,
            "url": item.get("jobUrl"),
            "title": item.get("title"),
            "company": "Nightfall AI",
            "location": location,
            "description": item.get("descriptionPlain"),
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "department": item.get("department"),
            "posted_at": item.get("publishedAt"),
            "external_id": item.get("id"),
            "apply_url": item.get("applyUrl"),
        }
        if locations:
            fields["locations"] = locations

        compensation = item.get("compensation") or {}
        components = [
            component
            for component in compensation.get("summaryComponents") or []
            if component.get("compensationType") == "Salary"
        ]
        if len(components) == 1:
            component = components[0]
            fields.update({
                "salary_min": component.get("minValue"),
                "salary_max": component.get("maxValue"),
                "salary_currency": component.get("currencyCode"),
                "salary_period": component.get("interval"),
            })
            salary_text = compensation.get("compensationTierSummary")
            if salary_text:
                fields["salary"] = salary_text

        yield {
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        }
