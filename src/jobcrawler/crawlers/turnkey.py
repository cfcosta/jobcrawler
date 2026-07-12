import json


SITE = "https://api.ashbyhq.com/posting-api/job-board/turnkey?includeCompensation=true"
EXTRA_HOSTS = []


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs", []):
        if posting.get("isListed") is not True:
            continue

        job = {
            "url": posting.get("jobUrl"),
            "title": posting.get("title"),
            "company": "Turnkey",
            "location": posting.get("location"),
            "description": posting.get("descriptionPlain"),
            "source": "api",
        }

        optional_fields = {
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        job.update({
            key: value
            for key, value in optional_fields.items()
            if value not in (None, "")
        })

        secondary_locations = posting.get("secondaryLocations") or []
        if secondary_locations:
            locations = [posting.get("location")]
            locations.extend(
                item.get("location")
                for item in secondary_locations
                if isinstance(item, dict) and item.get("location")
            )
            job["locations"] = [location for location in locations if location]

        if posting.get("shouldDisplayCompensationOnJobPostings"):
            compensation = posting.get("compensation") or {}
            salary_components = [
                component
                for component in compensation.get("summaryComponents", [])
                if component.get("compensationType") == "Salary"
            ]
            currencies = {
                component.get("currencyCode")
                for component in salary_components
                if component.get("currencyCode")
            }

            if len(currencies) == 1 and len(salary_components) == 1:
                component = salary_components[0]
                salary_fields = {
                    "salary": compensation.get("compensationTierSummary"),
                    "salary_min": component.get("minValue"),
                    "salary_max": component.get("maxValue"),
                    "salary_currency": component.get("currencyCode"),
                    "salary_period": component.get("interval"),
                }
                job.update({
                    key: value
                    for key, value in salary_fields.items()
                    if value not in (None, "")
                })

        yield job
