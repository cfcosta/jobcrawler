import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/vastai?includeCompensation=true'
EXTRA_HOSTS = []


def _salary_fields(job):
    compensation = job.get("compensation") or {}
    for tier in compensation.get("compensationTiers") or []:
        for component in tier.get("components") or []:
            if component.get("compensationType") != "Salary":
                continue
            minimum = component.get("minValue")
            maximum = component.get("maxValue")
            currency = component.get("currencyCode")
            period = component.get("interval")
            if minimum is None and maximum is None:
                continue
            fields = {}
            if component.get("summary"):
                fields["salary"] = component["summary"]
            if minimum is not None:
                fields["salary_min"] = minimum
            if maximum is not None:
                fields["salary_max"] = maximum
            if currency:
                fields["salary_currency"] = currency
            if period:
                fields["salary_period"] = period
            return fields
    return {}


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
            value = secondary.get("location")
            if value and value not in locations:
                locations.append(value)

        fields = {
            "url": item.get("jobUrl"),
            "title": item.get("title"),
            "company": "Vast.ai",
            "location": location,
            "description": item.get("descriptionPlain"),
            "external_id": item.get("id"),
            "source": SITE,
        }
        optional = {
            "locations": locations or None,
            "apply_url": item.get("applyUrl"),
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "department": item.get("department"),
            "posted_at": item.get("publishedAt"),
        }
        fields.update({key: value for key, value in optional.items() if value is not None})
        fields.update(_salary_fields(item))
        yield fields
