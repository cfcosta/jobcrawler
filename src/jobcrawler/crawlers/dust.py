import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/dust?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs", []):
        if posting.get("isListed") is not True:
            continue

        location = posting.get("location")
        locations = []
        if location:
            locations.append(location)
        for secondary in posting.get("secondaryLocations") or []:
            secondary_location = secondary.get("location")
            if secondary_location:
                locations.append(secondary_location)

        kwargs = {
            "url": posting.get("jobUrl"),
            "source": SITE,
            "title": posting.get("title"),
            "company": "Dust",
            "location": location,
            "description": posting.get("descriptionPlain"),
            "external_id": posting.get("id"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
            "apply_url": posting.get("applyUrl"),
        }

        if locations:
            kwargs["locations"] = locations

        compensation = posting.get("compensation") or {}
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

        if len(salary_components) == 1 and len(currencies) == 1:
            salary_component = salary_components[0]
            kwargs["salary"] = (
                compensation.get("compensationTierSummary")
                or compensation.get("scrapeableCompensationSalarySummary")
            )
            kwargs["salary_min"] = salary_component.get("minValue")
            kwargs["salary_max"] = salary_component.get("maxValue")
            kwargs["salary_currency"] = salary_component.get("currencyCode")
            kwargs["salary_period"] = salary_component.get("interval")

        yield {key: value for key, value in kwargs.items() if value is not None}
