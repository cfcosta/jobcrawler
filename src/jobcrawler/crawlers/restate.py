import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/restate?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for item in data.get("jobs", []):
        if item.get("isListed") is False:
            continue

        title = item.get("title")
        location = item.get("location")
        description = item.get("descriptionPlain")
        url = item.get("jobUrl")
        if not title or not location or not description or not url:
            continue

        job = {
            "url": url,
            "title": title,
            "company": "Restate",
            "location": location,
            "description": description,
            "source": SITE,
        }

        locations = [location]
        for secondary in item.get("secondaryLocations") or []:
            secondary_location = secondary.get("location")
            if secondary_location and secondary_location not in locations:
                locations.append(secondary_location)
        job["locations"] = locations

        optional_fields = {
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "department": item.get("department"),
            "posted_at": item.get("publishedAt"),
            "external_id": item.get("id"),
            "apply_url": item.get("applyUrl"),
        }
        for key, value in optional_fields.items():
            if value is not None and value != "":
                job[key] = value

        compensation = item.get("compensation") or {}
        salary_text = compensation.get("compensationTierSummary")
        if salary_text:
            job["salary"] = salary_text

        salary_components = [
            component
            for component in compensation.get("summaryComponents") or []
            if component.get("compensationType") == "Salary"
        ]
        if len(salary_components) == 1:
            component = salary_components[0]
            salary_min = component.get("minValue")
            salary_max = component.get("maxValue")
            currency = component.get("currencyCode")
            period = component.get("interval")
            if salary_min is not None and salary_max is not None and currency and period:
                job["salary_min"] = salary_min
                job["salary_max"] = salary_max
                job["salary_currency"] = currency
                job["salary_period"] = period

        yield job
