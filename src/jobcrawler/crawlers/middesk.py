import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/middesk?includeCompensation=true'
EXTRA_HOSTS = []


def _salary_fields(item):
    compensation = item.get("compensation") or {}
    fields = {}

    printed = compensation.get("compensationTierSummary")
    if printed:
        fields["salary"] = printed

    for component in compensation.get("summaryComponents") or []:
        if component.get("compensationType") != "Salary":
            continue
        minimum = component.get("minValue")
        maximum = component.get("maxValue")
        currency = component.get("currencyCode")
        period = component.get("interval")
        if minimum is not None:
            fields["salary_min"] = minimum
        if maximum is not None:
            fields["salary_max"] = maximum
        if currency:
            fields["salary_currency"] = currency
        if period:
            fields["salary_period"] = period
        break

    return fields


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for item in payload.get("jobs") or []:
        if item.get("isListed") is False:
            continue

        primary_location = item.get("location")
        locations = []
        if primary_location:
            locations.append(primary_location)
        for secondary in item.get("secondaryLocations") or []:
            location = secondary.get("location")
            if location:
                locations.append(location)

        fields = {
            "url": item.get("jobUrl"),
            "title": item.get("title"),
            "company": "Middesk",
            "location": primary_location,
            "description": item.get("descriptionPlain"),
            "source": SITE,
            "locations": locations or None,
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "department": item.get("department"),
            "posted_at": item.get("publishedAt"),
            "external_id": item.get("id"),
            "apply_url": item.get("applyUrl"),
        }
        fields.update(_salary_fields(item))
        yield {key: value for key, value in fields.items() if value is not None}
