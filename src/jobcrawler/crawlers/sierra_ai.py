import json


SITE = "https://api.ashbyhq.com/posting-api/job-board/sierra?includeCompensation=true"
EXTRA_HOSTS = []


def crawl(ctx):
    response = ctx.fetch(SITE)
    data = json.loads(response)

    seen = set()
    for item in data.get("jobs", []):
        external_id = item.get("id")
        if external_id in seen:
            continue
        seen.add(external_id)

        kwargs = {
            "url": item["jobUrl"],
            "title": item["title"],
            "company": "Sierra",
            "location": item["location"],
            "description": item["descriptionPlain"],
            "source": SITE,
            "external_id": external_id,
        }

        secondary = [
            entry["location"]
            for entry in item.get("secondaryLocations", [])
            if entry.get("location")
        ]
        kwargs["locations"] = [item["location"]] + secondary

        optional_fields = {
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "department": item.get("department"),
            "posted_at": item.get("publishedAt"),
            "apply_url": item.get("applyUrl"),
        }
        kwargs.update({key: value for key, value in optional_fields.items() if value is not None})

        compensation = item.get("compensation") or {}
        salary_components = [
            component
            for component in compensation.get("summaryComponents", [])
            if component.get("compensationType") == "Salary"
        ]
        if len(salary_components) == 1:
            component = salary_components[0]
            salary_fields = {
                "salary": compensation.get("scrapeableCompensationSalarySummary"),
                "salary_min": component.get("minValue"),
                "salary_max": component.get("maxValue"),
                "salary_currency": component.get("currencyCode"),
                "salary_period": component.get("interval"),
            }
            kwargs.update({key: value for key, value in salary_fields.items() if value is not None})

        yield kwargs
