import json

from jobcrawler.runtime.contract import Job

SITE = 'https://api.ashbyhq.com/posting-api/job-board/dourolabs.xyz?includeCompensation=true'
EXTRA_HOSTS = []


def _compensation_fields(job):
    compensation = job.get("compensation") or {}
    components = compensation.get("summaryComponents") or []
    salary_components = [
        component for component in components
        if component.get("minValue") is not None
        or component.get("maxValue") is not None
    ]
    if not salary_components:
        return {}

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
        return {}

    result = {}
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
        result["salary_min"] = min(minimums)
    if maximums:
        result["salary_max"] = max(maximums)
    if currencies:
        result["salary_currency"] = next(iter(currencies))
    if intervals:
        result["salary_period"] = next(iter(intervals))

    printed = compensation.get("compensationTierSummary")
    if printed:
        result["salary"] = printed
    return result


def crawl(ctx):
    response = ctx.fetch(SITE)
    payload = json.loads(response)

    for posting in payload.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        title = posting.get("title")
        location = posting.get("location")
        description = posting.get("descriptionPlain")
        url = posting.get("jobUrl")
        if not title or not location or not description or not url:
            continue

        locations = [location]
        for secondary in posting.get("secondaryLocations") or []:
            secondary_location = secondary.get("location")
            if secondary_location and secondary_location not in locations:
                locations.append(secondary_location)

        fields = {
            "url": url,
            "title": title,
            "company": "Douro Labs",
            "location": location,
            "locations": locations,
            "description": description,
            "source": SITE,
        }

        optional = {
            "department": posting.get("department") or posting.get("team"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "posted_at": posting.get("publishedAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        fields.update({key: value for key, value in optional.items() if value is not None})
        fields.update(_compensation_fields(posting))
        yield Job(**fields)
