import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/socket?includeCompensation=true"
EXTRA_HOSTS = []


def crawl(ctx):
    raw = ctx.fetch(SITE)
    data = json.loads(raw)

    for item in data.get("jobs", []):
        if not item.get("isListed"):
            continue

        fields = {
            "url": item.get("jobUrl"),
            "source": SITE,
            "title": item.get("title"),
            "company": "Socket",
            "location": item.get("location"),
            "description": item.get("descriptionPlain"),
            "external_id": item.get("id"),
            "apply_url": item.get("applyUrl"),
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "department": item.get("department"),
            "posted_at": item.get("publishedAt"),
        }

        locations = []
        if item.get("location"):
            locations.append(item["location"])
        for secondary in item.get("secondaryLocations") or []:
            value = secondary.get("location") if isinstance(secondary, dict) else secondary
            if value and value not in locations:
                locations.append(value)
        if locations:
            fields["locations"] = locations

        compensation = item.get("compensation") or {}
        if item.get("shouldDisplayCompensationOnJobPostings"):
            printed = compensation.get("compensationTierSummary")
            if printed:
                fields["salary"] = printed

            salary_components = [
                component
                for component in compensation.get("summaryComponents") or []
                if component.get("compensationType") == "Salary"
                and component.get("currencyCode")
                and (component.get("minValue") is not None
                     or component.get("maxValue") is not None)
            ]
            if len(salary_components) == 1:
                component = salary_components[0]
                fields["salary_min"] = component.get("minValue")
                fields["salary_max"] = component.get("maxValue")
                fields["salary_currency"] = component.get("currencyCode")
                fields["salary_period"] = component.get("interval")

        fields = {key: value for key, value in fields.items() if value is not None}
        yield fields
