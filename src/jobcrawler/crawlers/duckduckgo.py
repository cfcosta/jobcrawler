import json


SITE = 'https://api.ashbyhq.com/posting-api/job-board/duck-duck-go?includeCompensation=true'
EXTRA_HOSTS = []


def _salary_fields(posting):
    compensation = posting.get("compensation") or {}
    components = compensation.get("summaryComponents") or []
    salary_components = [
        component
        for component in components
        if component.get("compensationType") == "Salary"
    ]

    currencies = {
        component.get("currencyCode")
        for component in salary_components
        if component.get("currencyCode")
    }
    if not salary_components or len(currencies) > 1:
        return {}

    component = salary_components[0]
    fields = {}

    printed = compensation.get("scrapeableCompensationSalarySummary")
    if printed:
        fields["salary"] = printed
    if component.get("minValue") is not None:
        fields["salary_min"] = component["minValue"]
    if component.get("maxValue") is not None:
        fields["salary_max"] = component["maxValue"]
    if component.get("currencyCode"):
        fields["salary_currency"] = component["currencyCode"]
    if component.get("interval"):
        fields["salary_period"] = component["interval"]

    return fields


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for posting in payload.get("jobs", []):
        if posting.get("isListed") is not True:
            continue

        title = posting.get("title")
        url = posting.get("jobUrl")
        location = posting.get("location")
        description = posting.get("descriptionPlain")
        if not title or not url or not location or not description:
            continue

        locations = [location]
        for secondary in posting.get("secondaryLocations") or []:
            secondary_location = secondary.get("location")
            if secondary_location and secondary_location not in locations:
                locations.append(secondary_location)

        fields = {
            "url": url,
            "source": SITE,
            "title": title,
            "company": "DuckDuckGo",
            "location": location,
            "locations": locations,
            "description": description,
        }

        optional = {
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department") or posting.get("team"),
            "posted_at": posting.get("publishedAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        fields.update({key: value for key, value in optional.items() if value})
        fields.update(_salary_fields(posting))

        yield fields
