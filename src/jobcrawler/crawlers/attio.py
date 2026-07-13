import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/attio?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for item in data.get("jobs", []):
        if not item.get("isListed", True):
            continue

        kwargs = {
            "url": item["jobUrl"],
            "title": item["title"],
            "company": "Attio",
            "location": item["location"],
            "description": item["descriptionPlain"],
            "source": SITE,
        }

        optional = {
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "department": item.get("department"),
            "posted_at": item.get("publishedAt"),
            "external_id": item.get("id"),
            "apply_url": item.get("applyUrl"),
        }
        kwargs.update({
            key: value
            for key, value in optional.items()
            if value is not None
        })

        secondary = [
            entry.get("location")
            for entry in item.get("secondaryLocations", [])
            if entry.get("location")
        ]
        if secondary:
            kwargs["locations"] = [item["location"]] + secondary

        compensation = item.get("compensation") or {}
        salary_components = [
            component
            for component in compensation.get("summaryComponents", [])
            if component.get("compensationType") == "Salary"
        ]
        if len(salary_components) == 1:
            component = salary_components[0]
            salary_fields = {
                "salary_min": component.get("minValue"),
                "salary_max": component.get("maxValue"),
                "salary_currency": component.get("currencyCode"),
                "salary_period": component.get("interval"),
                "salary": compensation.get(
                    "scrapeableCompensationSalarySummary"
                ),
            }
            kwargs.update({
                key: value
                for key, value in salary_fields.items()
                if value is not None
            })

        yield kwargs
