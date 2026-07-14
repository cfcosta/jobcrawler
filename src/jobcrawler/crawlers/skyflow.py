import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/skyflow?includeCompensation=true"
EXTRA_HOSTS = []


def _salary_fields(job):
    compensation = job.get("compensation") or {}
    components = compensation.get("summaryComponents") or []

    for component in components:
        if component.get("compensationType") != "Salary":
            continue
        minimum = component.get("minValue")
        maximum = component.get("maxValue")
        currency = component.get("currencyCode")
        period = component.get("interval")
        if minimum is None or maximum is None or not currency or not period:
            continue

        fields = {
            "salary_min": minimum,
            "salary_max": maximum,
            "salary_currency": currency,
            "salary_period": period,
        }
        printed = compensation.get("scrapeableCompensationSalarySummary")
        if printed:
            fields["salary"] = printed
        return fields

    return {}


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        location = posting.get("location")
        description = posting.get("descriptionPlain")
        fields = {
            "url": posting.get("jobUrl"),
            "title": posting.get("title"),
            "company": "Skyflow",
            "location": location,
            "description": description,
            "source": SITE,
        }

        optional = {
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        fields.update({key: value for key, value in optional.items() if value})

        locations = []
        if location:
            locations.append(location)
        for secondary in posting.get("secondaryLocations") or []:
            value = secondary.get("location") if isinstance(secondary, dict) else secondary
            if value:
                locations.append(value)
        if locations:
            fields["locations"] = locations

        fields.update(_salary_fields(posting))
        yield fields
