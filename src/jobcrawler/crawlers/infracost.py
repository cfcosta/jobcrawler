import json
SITE = 'https://api.ashbyhq.com/posting-api/job-board/infracost?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    response = ctx.fetch(SITE)
    payload = json.loads(response)

    for item in payload.get("jobs", []):
        if not item.get("isListed", True):
            continue

        location = item.get("location")
        locations = []
        if location:
            locations.append(location)
        for secondary in item.get("secondaryLocations") or []:
            name = secondary.get("location")
            if name and name not in locations:
                locations.append(name)

        fields = {
            "url": item.get("jobUrl"),
            "source": SITE,
            "title": item.get("title"),
            "company": "Infracost",
            "location": location,
            "locations": locations or None,
            "description": item.get("descriptionPlain"),
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "department": item.get("department") or item.get("team"),
            "posted_at": item.get("publishedAt"),
            "external_id": item.get("id"),
            "apply_url": item.get("applyUrl"),
        }

        compensation = item.get("compensation") or {}
        salary_components = [
            component
            for component in compensation.get("summaryComponents") or []
            if component.get("compensationType") == "Salary"
        ]
        currencies = {
            component.get("currencyCode")
            for component in salary_components
            if component.get("currencyCode")
        }
        if salary_components and len(currencies) <= 1:
            component = salary_components[0]
            fields.update(
                salary=compensation.get("compensationTierSummary"),
                salary_min=component.get("minValue"),
                salary_max=component.get("maxValue"),
                salary_currency=component.get("currencyCode"),
                salary_period=component.get("interval"),
            )

        yield {key: value for key, value in fields.items() if value is not None}
