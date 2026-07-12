
import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/Deepgram?includeCompensation=true"
EXTRA_HOSTS = []


def _salary_fields(job):
    compensation = job.get("compensation") or {}
    components = compensation.get("summaryComponents") or []
    salaries = [
        component
        for component in components
        if component.get("compensationType") == "Salary"
    ]
    if not salaries:
        return {}

    currencies = {
        component.get("currencyCode")
        for component in salaries
        if component.get("currencyCode")
    }
    intervals = {
        component.get("interval")
        for component in salaries
        if component.get("interval")
    }
    if len(currencies) > 1 or len(intervals) > 1:
        return {}

    component = salaries[0]
    fields = {}
    printed = compensation.get("scrapeableCompensationSalarySummary")
    if printed:
        fields["salary"] = printed
    if component.get("minValue") is not None:
        fields["salary_min"] = component["minValue"]
    if component.get("maxValue") is not None:
        fields["salary_max"] = component["maxValue"]
    if component.get("currencyCode"):
        fields["salary_currency"] = component["currencyCode"]
    if component.get("interval"):
        fields["salary_period"] = component["interval"]
    return fields


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))
    for posting in payload.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        location = posting.get("location")
        locations = []
        if location:
            locations.append(location)
        for secondary in posting.get("secondaryLocations") or []:
            if isinstance(secondary, dict):
                secondary_location = secondary.get("location")
            else:
                secondary_location = secondary
            if secondary_location and secondary_location not in locations:
                locations.append(secondary_location)

        workplace_type = posting.get("workplaceType")
        if not workplace_type and posting.get("isRemote"):
            workplace_type = "Remote"

        fields = {
            "url": posting.get("jobUrl"),
            "title": posting.get("title"),
            "company": "Deepgram",
            "source": SITE,
            "location": location,
            "description": posting.get("descriptionPlain"),
            "external_id": posting.get("id"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": workplace_type,
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
            "apply_url": posting.get("applyUrl"),
        }
        if locations:
            fields["locations"] = locations
        fields.update(_salary_fields(posting))

        yield {key: value for key, value in fields.items() if value is not None}
