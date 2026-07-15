import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/artie?includeCompensation=true'
EXTRA_HOSTS = []


def _locations(item):
    values = []
    primary = item.get("location")
    if primary:
        values.append(primary)

    for secondary in item.get("secondaryLocations") or []:
        if isinstance(secondary, dict):
            value = secondary.get("location")
        else:
            value = secondary
        if value and value not in values:
            values.append(value)
    return values


def _salary_fields(item):
    if not item.get("shouldDisplayCompensationOnJobPostings"):
        return {}

    compensation = item.get("compensation") or {}
    components = compensation.get("summaryComponents") or []
    salaries = [
        component for component in components
        if component.get("compensationType") == "Salary"
    ]
    if len(salaries) != 1:
        return {}

    component = salaries[0]
    result = {}
    printed = compensation.get("compensationTierSummary")
    if printed:
        result["salary"] = printed
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
    payload = ctx.fetch(SITE)
    if isinstance(payload, str):
        payload = json.loads(payload)

    for item in payload.get("jobs") or []:
        if not item.get("isListed", True):
            continue

        job = {
            "url": item["jobUrl"],
            "title": item["title"],
            "company": "Artie",
            "location": item["location"],
            "description": item["descriptionPlain"],
            "source": SITE,
        }

        locations = _locations(item)
        if locations:
            job["locations"] = locations

        optional = {
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "department": item.get("department"),
            "posted_at": item.get("publishedAt"),
            "external_id": item.get("id"),
            "apply_url": item.get("applyUrl"),
        }
        for key, value in optional.items():
            if value is not None and value != "":
                job[key] = value

        job.update(_salary_fields(item))
        yield job
