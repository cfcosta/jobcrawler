import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/ditto?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for item in data.get("jobs", []):
        if item.get("isListed") is False:
            continue

        location = item.get("location")
        locations = []
        if location:
            locations.append(location)
        for secondary in item.get("secondaryLocations") or []:
            secondary_location = secondary.get("location")
            if secondary_location and secondary_location not in locations:
                locations.append(secondary_location)

        fields = {
            "url": item["jobUrl"],
            "title": item["title"],
            "company": "Ditto",
            "location": location,
            "description": item["descriptionPlain"],
            "source": SITE,
            "external_id": item.get("id"),
            "apply_url": item.get("applyUrl"),
            "employment_type": item.get("employmentType"),
            "department": item.get("department"),
            "posted_at": item.get("publishedAt"),
        }

        if locations:
            fields["locations"] = locations

        workplace_type = item.get("workplaceType")
        if workplace_type:
            fields["workplace_type"] = workplace_type
        elif item.get("isRemote") is True:
            fields["workplace_type"] = "Remote"

        compensation = item.get("compensation") or {}
        salary_components = [
            component
            for component in compensation.get("summaryComponents") or []
            if component.get("compensationType") == "Salary"
        ]
        if len(salary_components) == 1:
            component = salary_components[0]
            if component.get("minValue") is not None:
                fields["salary_min"] = component["minValue"]
            if component.get("maxValue") is not None:
                fields["salary_max"] = component["maxValue"]
            if component.get("currencyCode"):
                fields["salary_currency"] = component["currencyCode"]
            if component.get("interval"):
                fields["salary_period"] = component["interval"]

            salary_text = compensation.get("compensationTierSummary")
            if salary_text:
                fields["salary"] = salary_text

        yield {key: value for key, value in fields.items() if value is not None}
