import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/kestra?includeCompensation=true'
EXTRA_HOSTS = []


def _compensation_fields(compensation):
    if not isinstance(compensation, dict):
        return {}

    fields = {}
    printed = (
        compensation.get("scrapeableCompensationSalarySummary")
        or compensation.get("compensationTierSummary")
    )
    if isinstance(printed, str) and printed.strip():
        fields["salary"] = printed.strip()

    components = compensation.get("summaryComponents") or []
    for component in components:
        if not isinstance(component, dict):
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

        if any(key in fields for key in (
            "salary_min", "salary_max", "salary_currency", "salary_period"
        )):
            break

    return fields


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for posting in payload.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        title = posting.get("title")
        location = posting.get("location")
        description = posting.get("descriptionPlain")
        url = posting.get("jobUrl")
        if not all((title, location, description, url)):
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Kestra",
            "location": location,
            "description": description,
            "source": "Ashby",
        }

        locations = [location]
        for secondary in posting.get("secondaryLocations") or []:
            secondary_location = secondary.get("location") if isinstance(secondary, dict) else None
            if secondary_location and secondary_location not in locations:
                locations.append(secondary_location)
        fields["locations"] = locations

        optional = {
            "department": posting.get("department"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "posted_at": posting.get("publishedAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        fields.update({key: value for key, value in optional.items() if value not in (None, "")})
        fields.update(_compensation_fields(posting.get("compensation")))

        yield fields
