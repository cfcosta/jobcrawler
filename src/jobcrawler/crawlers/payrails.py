import json


SITE = 'https://api.ashbyhq.com/posting-api/job-board/Payrails?includeCompensation=true'
EXTRA_HOSTS = []


def _secondary_location(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("location") or value.get("name")
    return None


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for posting in payload.get("jobs", []):
        if not posting.get("isListed", True):
            continue

        location = posting.get("location")
        locations = []
        if location:
            locations.append(location)

        for item in posting.get("secondaryLocations") or []:
            secondary = _secondary_location(item)
            if secondary and secondary not in locations:
                locations.append(secondary)

        fields = {
            "url": posting.get("jobUrl"),
            "title": posting.get("title"),
            "company": "Payrails",
            "location": location,
            "locations": locations or None,
            "description": posting.get("descriptionPlain"),
            "source": "ashby",
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }

        compensation = posting.get("compensation") or {}
        printed = (
            compensation.get("compensationTierSummary")
            or compensation.get("scrapeableCompensationSalarySummary")
        )
        if printed:
            fields["salary"] = printed

        components = compensation.get("summaryComponents") or []
        usable = [
            component for component in components
            if component.get("minValue") is not None
            or component.get("maxValue") is not None
        ]
        currencies = {
            component.get("currencyCode")
            for component in usable
            if component.get("currencyCode")
        }
        if usable and len(currencies) <= 1:
            component = usable[0]
            fields["salary_min"] = component.get("minValue")
            fields["salary_max"] = component.get("maxValue")
            fields["salary_currency"] = component.get("currencyCode")
            fields["salary_period"] = component.get("interval")

        yield {
            key: value for key, value in fields.items()
            if value is not None
        }
