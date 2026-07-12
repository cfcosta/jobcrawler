
import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/doppler?includeCompensation=true"
EXTRA_HOSTS = []


def _set(fields, key, value):
    if value is not None and (not isinstance(value, str) or value):
        fields[key] = value


def _secondary_location(value):
    if isinstance(value, dict):
        return value.get("location")
    return value


def _salary_fields(compensation):
    fields = {}
    if not isinstance(compensation, dict):
        return fields

    printed = (
        compensation.get("compensationTierSummary")
        or compensation.get("scrapeableCompensationSalarySummary")
    )
    _set(fields, "salary", printed)

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
        _set(fields, "salary_currency", currency)
        _set(fields, "salary_period", period)
        break

    return fields


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for source in payload.get("jobs", []):
        if source.get("isListed") is not True:
            continue

        location = source.get("location")
        fields = {
            "url": source.get("jobUrl"),
            "title": source.get("title"),
            "company": "Doppler",
            "source": SITE,
            "location": location,
            "description": source.get("descriptionPlain"),
        }

        locations = []
        if location:
            locations.append(location)
        for secondary in source.get("secondaryLocations") or []:
            value = _secondary_location(secondary)
            if value and value not in locations:
                locations.append(value)
        if locations:
            fields["locations"] = locations

        _set(fields, "apply_url", source.get("applyUrl"))
        _set(fields, "external_id", source.get("id"))
        _set(fields, "posted_at", source.get("publishedAt"))
        _set(fields, "employment_type", source.get("employmentType"))
        _set(fields, "department", source.get("department") or source.get("team"))

        workplace_type = source.get("workplaceType")
        if not workplace_type and source.get("isRemote") is True:
            workplace_type = "Remote"
        _set(fields, "workplace_type", workplace_type)

        fields.update(_salary_fields(source.get("compensation")))
        yield fields
