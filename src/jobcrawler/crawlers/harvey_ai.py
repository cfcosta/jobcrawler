import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/harvey?includeCompensation=true"
EXTRA_HOSTS = []


def _locations(job):
    values = []
    primary = job.get("location")
    if primary:
        values.append(primary)

    for item in job.get("secondaryLocations") or []:
        if isinstance(item, dict):
            value = item.get("location")
        else:
            value = item
        if value and value not in values:
            values.append(value)

    return values


def _salary_fields(job):
    compensation = job.get("compensation") or {}
    if not job.get("shouldDisplayCompensationOnJobPostings"):
        return {}

    salary_components = [
        component
        for component in compensation.get("summaryComponents") or []
        if component.get("compensationType") == "Salary"
        and component.get("currencyCode")
        and (component.get("minValue") is not None
             or component.get("maxValue") is not None)
    ]
    if not salary_components:
        return {}

    currencies = {component["currencyCode"] for component in salary_components}
    intervals = {component.get("interval") for component in salary_components}
    if len(currencies) != 1 or len(intervals) != 1:
        return {}

    component = salary_components[0]
    result = {
        "salary_currency": component["currencyCode"],
        "salary_period": component.get("interval"),
    }
    if component.get("minValue") is not None:
        result["salary_min"] = component["minValue"]
    if component.get("maxValue") is not None:
        result["salary_max"] = component["maxValue"]

    printed = compensation.get("compensationTierSummary")
    if printed:
        result["salary"] = printed
    return result


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for posting in payload.get("jobs") or []:
        if not posting.get("isListed"):
            continue

        locations = _locations(posting)
        location = posting.get("location")
        description = posting.get("descriptionPlain")
        if not posting.get("title") or not location or not description:
            continue

        fields = {
            "url": posting.get("jobUrl"),
            "title": posting["title"],
            "company": "Harvey",
            "source": SITE,
            "location": location,
            "description": description,
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
        yield {key: value for key, value in fields.items() if value is not None}
