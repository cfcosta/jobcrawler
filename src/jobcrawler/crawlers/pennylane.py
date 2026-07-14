import json


SITE = 'https://api.ashbyhq.com/posting-api/job-board/pennylane?includeCompensation=true'
EXTRA_HOSTS = []


def _salary_fields(job):
    compensation = job.get("compensation") or {}
    components = compensation.get("summaryComponents") or []

    for component in components:
        minimum = component.get("minValue")
        maximum = component.get("maxValue")
        currency = component.get("currencyCode")
        period = component.get("interval")

        if minimum is None and maximum is None:
            continue

        fields = {}
        if minimum is not None:
            fields["salary_min"] = minimum
        if maximum is not None:
            fields["salary_max"] = maximum
        if currency:
            fields["salary_currency"] = currency
        if period:
            fields["salary_period"] = period

        printed = (
            component.get("summary")
            or component.get("displayValue")
            or component.get("text")
        )
        if printed:
            fields["salary"] = printed
        return fields

    return {}


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for posting in payload.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        title = posting.get("title")
        description = posting.get("descriptionPlain")
        url = posting.get("jobUrl")
        location = posting.get("location")

        if not title or not description or not url or not location:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Pennylane",
            "location": location,
            "description": description,
            "source": "Ashby",
        }

        optional = {
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        fields.update({
            key: value
            for key, value in optional.items()
            if value is not None
        })

        secondary = posting.get("secondaryLocations") or []
        if secondary:
            locations = [location]
            locations.extend(
                item["location"]
                for item in secondary
                if isinstance(item, dict) and item.get("location")
            )
            fields["locations"] = locations

        fields.update(_salary_fields(posting))
        yield fields
