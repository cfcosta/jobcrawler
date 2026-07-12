import json


SITE = 'https://api.ashbyhq.com/posting-api/job-board/injective?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    response = ctx.fetch(SITE)
    data = json.loads(response)

    for item in data.get("jobs", []):
        if not item.get("isListed", True):
            continue

        location = item.get("location")
        locations = []
        if location:
            locations.append(location)
        for secondary in item.get("secondaryLocations") or []:
            value = secondary.get("location") if isinstance(secondary, dict) else secondary
            if value and value not in locations:
                locations.append(value)

        kwargs = {
            "url": item.get("jobUrl"),
            "title": item.get("title"),
            "company": "Injective",
            "location": location,
            "description": item.get("descriptionPlain"),
            "source": SITE,
        }

        optional = {
            "locations": locations or None,
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "department": item.get("department") or item.get("team"),
            "posted_at": item.get("publishedAt"),
            "external_id": item.get("id"),
            "apply_url": item.get("applyUrl"),
        }
        kwargs.update({key: value for key, value in optional.items() if value is not None})

        compensation = item.get("compensation") or {}
        salary_text = (
            compensation.get("scrapeableCompensationSalarySummary")
            or compensation.get("compensationTierSummary")
        )
        if salary_text:
            kwargs["salary"] = salary_text

        salary_components = [
            component
            for component in compensation.get("summaryComponents") or []
            if component.get("compensationType") == "Salary"
        ]
        if len(salary_components) == 1:
            component = salary_components[0]
            salary_fields = {
                "salary_min": component.get("minValue"),
                "salary_max": component.get("maxValue"),
                "salary_currency": component.get("currencyCode"),
                "salary_period": component.get("interval"),
            }
            kwargs.update({
                key: value for key, value in salary_fields.items() if value is not None
            })

        yield kwargs
