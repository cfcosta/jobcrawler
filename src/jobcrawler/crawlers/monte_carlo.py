import json


SITE = 'https://api.ashbyhq.com/posting-api/job-board/montecarlodata?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for item in data.get("jobs", []):
        if item.get("isListed") is False:
            continue

        kwargs = {
            "url": item["jobUrl"],
            "title": item["title"],
            "company": "Monte Carlo",
            "location": item["location"],
            "description": item["descriptionPlain"],
            "external_id": item["id"],
            "source": SITE,
        }

        secondary = [
            entry.get("location")
            for entry in (item.get("secondaryLocations") or [])
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
        kwargs.update({
            key: value
            for key, value in optional_fields.items()
            if value is not None and value != ""
        })

        compensation = item.get("compensation") or {}
        salary_components = [
            component
            for component in (compensation.get("summaryComponents") or [])
            if component.get("compensationType") == "Salary"
            and component.get("minValue") is not None
            and component.get("maxValue") is not None
            and component.get("currencyCode")
            and component.get("interval")
        ]
        if salary_components:
            component = salary_components[0]
            kwargs.update({
                "salary_min": component["minValue"],
                "salary_max": component["maxValue"],
                "salary_currency": component["currencyCode"],
                "salary_period": component["interval"],
            })
            salary_text = compensation.get("compensationTierSummary")
            if salary_text:
                kwargs["salary"] = salary_text

        yield kwargs
