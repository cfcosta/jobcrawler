
import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/humaans?includeCompensation=true"
EXTRA_HOSTS = []


def _compensation_fields(job):
    compensation = job.get("compensation") or {}
    components = compensation.get("summaryComponents") or []
    salary_components = [
        component
        for component in components
        if component.get("minValue") is not None
        or component.get("maxValue") is not None
    ]

    fields = {}
    if len(salary_components) == 1:
        component = salary_components[0]
        if component.get("minValue") is not None:
            fields["salary_min"] = component["minValue"]
        if component.get("maxValue") is not None:
            fields["salary_max"] = component["maxValue"]
        if component.get("currencyCode"):
            fields["salary_currency"] = component["currencyCode"]
        if component.get("interval"):
            fields["salary_period"] = component["interval"]

    printed = (
        compensation.get("scrapeableCompensationSalarySummary")
        or compensation.get("compensationTierSummary")
    )
    if printed:
        fields["salary"] = printed

    return fields


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for item in payload.get("jobs", []):
        if item.get("isListed") is False:
            continue

        locations = []
        if item.get("location"):
            locations.append(item["location"])
        for secondary in item.get("secondaryLocations") or []:
            if isinstance(secondary, dict):
                value = secondary.get("location")
            else:
                value = secondary
            if value:
                locations.append(value)

        fields = {
            "url": item["jobUrl"],
            "title": item["title"],
            "company": "Humaans",
            "location": item["location"],
            "description": item["descriptionPlain"],
        }

        optional = {
            "locations": locations if len(locations) > 1 else None,
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "department": item.get("department") or item.get("team"),
            "posted_at": item.get("publishedAt"),
            "external_id": item.get("id"),
            "apply_url": item.get("applyUrl"),
        }
        fields.update({key: value for key, value in optional.items() if value is not None})
        fields.update(_compensation_fields(item))
        fields["source"] = SITE
        yield fields
