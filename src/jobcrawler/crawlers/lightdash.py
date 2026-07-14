import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/lightdash?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    response = ctx.fetch(SITE)
    data = response.json() if hasattr(response, "json") else json.loads(response)

    for posting in data.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        location = posting.get("location")
        locations = []
        if location:
            locations.append(location)
        for secondary in posting.get("secondaryLocations") or []:
            secondary_location = secondary.get("location")
            if secondary_location:
                locations.append(secondary_location)

        fields = {
            "url": posting.get("jobUrl"),
            "title": posting.get("title"),
            "company": "Lightdash",
            "location": location,
            "description": posting.get("descriptionPlain"),
            "source": SITE,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department") or posting.get("team"),
            "posted_at": posting.get("publishedAt"),
        }

        if locations:
            fields["locations"] = locations

        compensation = posting.get("compensation") or {}
        salary_text = compensation.get("scrapeableCompensationSalarySummary")
        if salary_text:
            fields["salary"] = salary_text

        salary_components = [
            component
            for component in compensation.get("summaryComponents") or []
            if component.get("compensationType") == "Salary"
        ]
        if len(salary_components) == 1:
            salary_component = salary_components[0]
            if salary_component.get("minValue") is not None:
                fields["salary_min"] = salary_component["minValue"]
            if salary_component.get("maxValue") is not None:
                fields["salary_max"] = salary_component["maxValue"]
            if salary_component.get("currencyCode"):
                fields["salary_currency"] = salary_component["currencyCode"]
            if salary_component.get("interval"):
                fields["salary_period"] = salary_component["interval"]

        yield {key: value for key, value in fields.items() if value is not None}
