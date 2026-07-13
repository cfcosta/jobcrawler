import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/readme?includeCompensation=true"
EXTRA_HOSTS = []


def _compensation_fields(job):
    compensation = job.get("compensation") or {}
    result = {}

    printed = (
        compensation.get("compensationTierSummary")
        or compensation.get("scrapeableCompensationSalarySummary")
    )
    if printed:
        result["salary"] = printed

    components = compensation.get("summaryComponents") or []
    salary_components = [
        component
        for component in components
        if component.get("minValue") is not None
        or component.get("maxValue") is not None
    ]
    if not salary_components:
        return result

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
        return result

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
    return result


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))
    for posting in payload.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        locations = []
        if posting.get("location"):
            locations.append(posting["location"])
        for secondary in posting.get("secondaryLocations") or []:
            value = secondary.get("location")
            if value and value not in locations:
                locations.append(value)

        fields = {
            "url": posting["jobUrl"],
            "title": posting["title"],
            "company": "ReadMe",
            "location": posting["location"],
            "locations": locations,
            "description": posting["descriptionPlain"],
            "source": SITE,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
        }
        fields.update(_compensation_fields(posting))
        yield {key: value for key, value in fields.items() if value is not None}
