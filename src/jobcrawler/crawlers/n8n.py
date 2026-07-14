import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/n8n?includeCompensation=true'
EXTRA_HOSTS = []


def _salary_fields(posting):
    compensation = posting.get("compensation") or {}
    components = compensation.get("summaryComponents") or []
    salary_components = [
        component
        for component in components
        if component.get("compensationType") == "Salary"
        and (component.get("minValue") is not None or component.get("maxValue") is not None)
    ]
    if not salary_components:
        return {}

    currencies = {component.get("currencyCode") for component in salary_components}
    periods = {component.get("interval") for component in salary_components}
    if None in currencies or len(currencies) != 1 or None in periods or len(periods) != 1:
        return {}

    component = salary_components[0]
    result = {
        "salary_currency": component["currencyCode"],
        "salary_period": component["interval"],
    }
    if component.get("minValue") is not None:
        result["salary_min"] = component["minValue"]
    if component.get("maxValue") is not None:
        result["salary_max"] = component["maxValue"]

    printed = compensation.get("scrapeableCompensationSalarySummary")
    if printed:
        result["salary"] = printed
    return result


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        primary_location = posting.get("location")
        locations = []
        if primary_location:
            locations.append(primary_location)
        for secondary in posting.get("secondaryLocations") or []:
            value = secondary.get("location")
            if value:
                locations.append(value)

        fields = {
            "url": posting.get("jobUrl"),
            "source": SITE,
            "title": posting.get("title"),
            "company": "n8n",
            "location": primary_location,
            "description": posting.get("descriptionPlain"),
            "external_id": posting.get("id"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
            "apply_url": posting.get("applyUrl"),
        }
        if locations:
            fields["locations"] = locations
        fields.update(_salary_fields(posting))

        yield {key: value for key, value in fields.items() if value is not None and value != ""}
