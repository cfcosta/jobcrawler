import json



SITE = "https://api.ashbyhq.com/posting-api/job-board/flashpoint.io?includeCompensation=true"
EXTRA_HOSTS = []


def _present(value):
    return value is not None and value != ""


def _compensation_fields(job):
    compensation = job.get("compensation") or {}
    components = compensation.get("summaryComponents") or []

    salary_components = [
        component
        for component in components
        if _present(component.get("minValue"))
        or _present(component.get("maxValue"))
    ]

    # A single coherent component can be represented by Job's salary quartet.
    if not salary_components:
        return {}

    currencies = {
        component.get("currencyCode")
        for component in salary_components
        if _present(component.get("currencyCode"))
    }
    intervals = {
        component.get("interval")
        for component in salary_components
        if _present(component.get("interval"))
    }
    if len(currencies) > 1 or len(intervals) > 1:
        return {}

    component = salary_components[0]
    fields = {}

    printed = (
        compensation.get("scrapeableCompensationSalarySummary")
        or compensation.get("compensationTierSummary")
    )
    if isinstance(printed, str) and printed.strip():
        fields["salary"] = printed

    if _present(component.get("minValue")):
        fields["salary_min"] = component["minValue"]
    if _present(component.get("maxValue")):
        fields["salary_max"] = component["maxValue"]
    if _present(component.get("currencyCode")):
        fields["salary_currency"] = component["currencyCode"]
    if _present(component.get("interval")):
        fields["salary_period"] = component["interval"]

    return fields


def crawl(ctx):
    response = ctx.fetch(SITE)
    payload = json.loads(response)
    for posting in payload.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        description = posting.get("descriptionPlain")
        if not _present(description):
            continue

        fields = {
            "url": posting["jobUrl"],
            "title": posting["title"],
            "company": "Flashpoint",
            "location": posting["location"],
            "description": description,
            "source": SITE,
        }

        optional_fields = {
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        fields.update({
            key: value
            for key, value in optional_fields.items()
            if _present(value)
        })

        secondary_locations = posting.get("secondaryLocations") or []
        if secondary_locations:
            locations = [posting["location"]]
            locations.extend(
                secondary["location"]
                for secondary in secondary_locations
                if _present(secondary.get("location"))
            )
            fields["locations"] = locations

        fields.update(_compensation_fields(posting))
        yield fields
