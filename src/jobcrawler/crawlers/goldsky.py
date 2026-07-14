
import json


SITE = "https://api.ashbyhq.com/posting-api/job-board/goldsky?includeCompensation=true"
EXTRA_HOSTS = []


def _compensation_fields(job):
    compensation = job.get("compensation") or {}
    fields = {}

    printed = (
        compensation.get("compensationTierSummary")
        or compensation.get("scrapeableCompensationSalarySummary")
    )
    if printed:
        fields["salary"] = printed

    components = compensation.get("summaryComponents") or []
    salary_components = [
        component
        for component in components
        if component.get("minValue") is not None
        or component.get("maxValue") is not None
    ]
    if not salary_components:
        return fields

    currencies = {
        component.get("currencyCode")
        for component in salary_components
        if component.get("currencyCode")
    }
    intervals = {
        component.get("interval")
        for component in salary_components
        if component.get("interval")
    }
    if len(currencies) > 1 or len(intervals) > 1:
        return fields

    minimums = [
        component["minValue"]
        for component in salary_components
        if component.get("minValue") is not None
    ]
    maximums = [
        component["maxValue"]
        for component in salary_components
        if component.get("maxValue") is not None
    ]
    if minimums:
        fields["salary_min"] = min(minimums)
    if maximums:
        fields["salary_max"] = max(maximums)
    if currencies:
        fields["salary_currency"] = next(iter(currencies))
    if intervals:
        fields["salary_period"] = next(iter(intervals))
    return fields


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for posting in payload.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        fields = {
            "url": posting.get("jobUrl"),
            "title": posting.get("title"),
            "company": "Goldsky",
            "location": posting.get("location"),
            "description": posting.get("descriptionPlain"),
            "source": SITE,
        }

        optional = {
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department") or posting.get("team"),
            "posted_at": posting.get("publishedAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        fields.update({key: value for key, value in optional.items() if value is not None})

        secondary = posting.get("secondaryLocations") or []
        if secondary:
            locations = [posting.get("location")] if posting.get("location") else []
            for entry in secondary:
                value = entry.get("location") if isinstance(entry, dict) else entry
                if value:
                    locations.append(value)
            if locations:
                fields["locations"] = locations

        fields.update(_compensation_fields(posting))
        yield fields
