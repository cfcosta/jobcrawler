import json


SITE = 'https://api.ashbyhq.com/posting-api/job-board/authzed?includeCompensation=true'
EXTRA_HOSTS = []


def _salary_fields(item):
    compensation = item.get("compensation") or {}
    components = compensation.get("summaryComponents") or []
    salary_components = [
        component
        for component in components
        if component.get("compensationType") == "Salary"
    ]
    if len(salary_components) != 1:
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
    data = json.loads(ctx.fetch(SITE))
    seen = set()

    for item in data.get("jobs", []):
        if not item.get("isListed", True):
            continue

        external_id = item.get("id")
        url = item.get("jobUrl")
        identity = external_id or url
        if not identity or identity in seen:
            continue
        seen.add(identity)

        title = item.get("title")
        location = item.get("location")
        description = item.get("descriptionPlain")
        if not title or not location or not description or not url:
            continue

        locations = [location]
        for secondary in item.get("secondaryLocations") or []:
            secondary_location = secondary.get("location")
            if secondary_location and secondary_location not in locations:
                locations.append(secondary_location)

        fields = {
            "url": url,
            "title": title,
            "company": "Authzed",
            "location": location,
            "locations": locations,
            "description": description.strip(),
            "source": SITE,
        }

        optional = {
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "department": item.get("department"),
            "posted_at": item.get("publishedAt"),
            "external_id": external_id,
            "apply_url": item.get("applyUrl"),
        }
        fields.update({
            key: value
            for key, value in optional.items()
            if value is not None and value != ""
        })
        fields.update(_salary_fields(item))

        yield fields
