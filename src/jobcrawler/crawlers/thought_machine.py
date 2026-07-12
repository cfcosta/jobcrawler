import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/thought-machine?includeCompensation=true'
EXTRA_HOSTS = []


def _locations(job):
    values = []
    primary = job.get("location")
    if primary:
        values.append(primary)

    for secondary in job.get("secondaryLocations") or []:
        if isinstance(secondary, str):
            value = secondary
        else:
            value = secondary.get("location")
        if value and value not in values:
            values.append(value)

    return values


def crawl(ctx):
    response = ctx.fetch(SITE)
    data = json.loads(response)

    for posting in data.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        description = posting.get("descriptionPlain")
        if not description:
            continue

        kwargs = {
            "url": posting.get("jobUrl"),
            "title": posting.get("title"),
            "company": "Thought Machine",
            "location": posting.get("location"),
            "description": description,
            "source": SITE,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
        }

        locations = _locations(posting)
        if locations:
            kwargs["locations"] = locations

        compensation = posting.get("compensation") or {}
        salary_components = [
            component
            for component in compensation.get("summaryComponents") or []
            if component.get("compensationType") == "Salary"
        ]
        if len(salary_components) == 1:
            component = salary_components[0]
            if component.get("minValue") is not None:
                kwargs["salary_min"] = component["minValue"]
            if component.get("maxValue") is not None:
                kwargs["salary_max"] = component["maxValue"]
            if component.get("currencyCode"):
                kwargs["salary_currency"] = component["currencyCode"]
            if component.get("interval"):
                kwargs["salary_period"] = component["interval"]

            printed_salary = compensation.get("compensationTierSummary")
            if printed_salary:
                kwargs["salary"] = printed_salary

        yield {key: value for key, value in kwargs.items() if value is not None}
