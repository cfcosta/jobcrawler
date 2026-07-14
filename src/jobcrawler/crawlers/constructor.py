import json


SITE = "https://api.ashbyhq.com/posting-api/job-board/constructor?includeCompensation=true"
EXTRA_HOSTS = []


def crawl(ctx):
    response = ctx.fetch(SITE)
    payload = json.loads(response)

    for posting in payload.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        primary_location = posting.get("location")
        locations = []
        if primary_location:
            locations.append(primary_location)
        for secondary in posting.get("secondaryLocations") or []:
            name = secondary.get("location")
            if name:
                locations.append(name)

        fields = {
            "url": posting["jobUrl"],
            "source": SITE,
            "title": posting["title"],
            "company": "Constructor",
            "location": primary_location,
            "description": posting.get("descriptionPlain"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
        }
        if locations:
            fields["locations"] = locations

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
        if len(salary_components) == 1 and len(currencies) <= 1:
            component = salary_components[0]
            fields["salary_min"] = component.get("minValue")
            fields["salary_max"] = component.get("maxValue")
            fields["salary_currency"] = component.get("currencyCode")
            fields["salary_period"] = component.get("interval")
            fields["salary"] = compensation.get("compensationTierSummary")

        yield {key: value for key, value in fields.items() if value is not None}
