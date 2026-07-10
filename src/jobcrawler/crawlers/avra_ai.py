
import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/avra?includeCompensation=true"
EXTRA_HOSTS = []


def _locations(job):
    values = []
    primary = job.get("location")
    if primary:
        values.append(primary)

    for secondary in job.get("secondaryLocations") or []:
        if isinstance(secondary, dict):
            value = secondary.get("location")
        else:
            value = secondary
        if value and value not in values:
            values.append(value)

    return values


def _compensation_fields(job):
    if not job.get("shouldDisplayCompensationOnJobPostings"):
        return {}

    compensation = job.get("compensation") or {}
    result = {}

    printed = (
        compensation.get("compensationTierSummary")
        or compensation.get("scrapeableCompensationSalarySummary")
    )
    if printed:
        result["salary"] = printed

    components = [
        component
        for component in compensation.get("summaryComponents") or []
        if isinstance(component, dict)
        and (component.get("minValue") is not None
             or component.get("maxValue") is not None)
    ]
    currencies = {
        component.get("currencyCode")
        for component in components
        if component.get("currencyCode")
    }

    if components and len(currencies) <= 1:
        component = components[0]
        if component.get("minValue") is not None:
            result["salary_min"] = component["minValue"]
        if component.get("maxValue") is not None:
            result["salary_max"] = component["maxValue"]
        if component.get("currencyCode"):
            result["salary_currency"] = component["currencyCode"]
        if component.get("interval"):
            result["salary_period"] = component["interval"]

    return result


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for item in payload.get("jobs") or []:
        if item.get("isListed") is not True:
            continue

        description = item.get("descriptionPlain")
        title = item.get("title")
        url = item.get("jobUrl")
        location = item.get("location")
        if not title or not url or not location or not description:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Avra",
            "location": location,
            "description": description,
            "source": SITE,
        }

        locations = _locations(item)
        if locations:
            fields["locations"] = locations
        if item.get("department"):
            fields["department"] = item["department"]
        if item.get("employmentType"):
            fields["employment_type"] = item["employmentType"]
        if item.get("workplaceType"):
            fields["workplace_type"] = item["workplaceType"]
        if item.get("publishedAt"):
            fields["posted_at"] = item["publishedAt"]
        if item.get("id"):
            fields["external_id"] = item["id"]
        if item.get("applyUrl"):
            fields["apply_url"] = item["applyUrl"]

        fields.update(_compensation_fields(item))
        yield fields
