import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/hawk?includeCompensation=true'
EXTRA_HOSTS = []


def _salary_fields(job):
    compensation = job.get("compensation") or {}
    components = compensation.get("summaryComponents") or []

    salary_component = next(
        (
            component
            for component in components
            if component.get("compensationType") == "Salary"
        ),
        None,
    )
    if not salary_component:
        return {}

    fields = {}
    field_map = {
        "minValue": "salary_min",
        "maxValue": "salary_max",
        "currencyCode": "salary_currency",
        "interval": "salary_period",
    }
    for source_key, target_key in field_map.items():
        value = salary_component.get(source_key)
        if value is not None and value != "":
            fields[target_key] = value

    printed_salary = (
        compensation.get("compensationTierSummary")
        or compensation.get("scrapeableCompensationSalarySummary")
    )
    if printed_salary:
        fields["salary"] = printed_salary

    return fields


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for posting in payload.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        title = posting.get("title")
        description = posting.get("descriptionPlain")
        location = posting.get("location")
        job_url = posting.get("jobUrl")
        if not all((title, description, location, job_url)):
            continue

        locations = [location]
        for secondary in posting.get("secondaryLocations") or []:
            secondary_location = secondary.get("location")
            if secondary_location:
                locations.append(secondary_location)

        fields = {
            "url": job_url,
            "title": title,
            "company": "Hawk",
            "location": location,
            "locations": locations,
            "description": description,
            "source": SITE,
        }

        optional_fields = {
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        fields.update({
            key: value
            for key, value in optional_fields.items()
            if value is not None and value != ""
        })
        fields.update(_salary_fields(posting))

        yield fields
