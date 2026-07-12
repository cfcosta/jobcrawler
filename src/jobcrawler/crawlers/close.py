
import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/close?includeCompensation=true"
EXTRA_HOSTS = []


def _locations(job):
    locations = []

    primary = job.get("location")
    if primary:
        locations.append(primary)

    for secondary in job.get("secondaryLocations") or []:
        if isinstance(secondary, str):
            location = secondary
        else:
            location = secondary.get("location")
        if location and location not in locations:
            locations.append(location)

    return locations


def _salary_fields(job):
    if not job.get("shouldDisplayCompensationOnJobPostings"):
        return {}

    compensation = job.get("compensation") or {}
    components = [
        component
        for component in compensation.get("summaryComponents") or []
        if component.get("compensationType") == "Salary"
    ]
    if not components:
        return {}

    currencies = {
        component.get("currencyCode")
        for component in components
        if component.get("currencyCode")
    }
    intervals = {
        component.get("interval")
        for component in components
        if component.get("interval")
    }
    if len(currencies) > 1 or len(intervals) > 1:
        return {}

    result = {}
    printed = (
        compensation.get("compensationTierSummary")
        or compensation.get("scrapeableCompensationSalarySummary")
    )
    if printed:
        result["salary"] = printed

    minimums = [
        component["minValue"]
        for component in components
        if component.get("minValue") is not None
    ]
    maximums = [
        component["maxValue"]
        for component in components
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

    for posting in payload.get("jobs") or []:
        if posting.get("isListed") is False:
            continue

        locations = _locations(posting)
        fields = {
            "url": posting.get("jobUrl"),
            "title": posting.get("title"),
            "company": "Close",
            "location": posting.get("location"),
            "description": posting.get("descriptionPlain"),
            "source": SITE,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department") or posting.get("team"),
            "posted_at": posting.get("publishedAt"),
        }
        if locations:
            fields["locations"] = locations
        fields.update(_salary_fields(posting))

        yield {
            key: value
            for key, value in fields.items()
            if value is not None
        }
