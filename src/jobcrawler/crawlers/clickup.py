import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/clickup?includeCompensation=true"
EXTRA_HOSTS = []


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for item in payload.get("jobs", []):
        if item.get("isListed") is False:
            continue

        primary_location = item.get("location")
        locations = []
        if primary_location:
            locations.append(primary_location)

        for secondary in item.get("secondaryLocations") or []:
            if isinstance(secondary, dict):
                secondary_location = secondary.get("location")
                if secondary_location:
                    locations.append(secondary_location)

        compensation = item.get("compensation") or {}
        salary_component = next(
            (
                component
                for component in compensation.get("summaryComponents") or []
                if component.get("compensationType") == "Salary"
            ),
            None,
        )

        job = {
            "url": item.get("jobUrl"),
            "title": item.get("title"),
            "company": "ClickUp",
            "location": primary_location,
            "locations": locations,
            "description": item.get("descriptionPlain"),
            "source": SITE,
            "department": item.get("department"),
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "posted_at": item.get("publishedAt"),
            "external_id": item.get("id"),
            "apply_url": item.get("applyUrl"),
            "salary": compensation.get("compensationTierSummary"),
        }

        if salary_component:
            job.update({
                "salary_min": salary_component.get("minValue"),
                "salary_max": salary_component.get("maxValue"),
                "salary_currency": salary_component.get("currencyCode"),
                "salary_period": salary_component.get("interval"),
            })

        yield {
            key: value
            for key, value in job.items()
            if value is not None and value != "" and value != []
        }
