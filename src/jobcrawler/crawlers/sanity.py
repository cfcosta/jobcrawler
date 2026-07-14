
import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/sanity?includeCompensation=true"
EXTRA_HOSTS = []


def _salary_fields(job):
    compensation = job.get("compensation") or {}
    components = compensation.get("summaryComponents") or []

    salary_component = None
    for component in components:
        compensation_type = str(component.get("compensationType") or "").lower()
        if compensation_type in ("salary", "base salary", "base"):
            salary_component = component
            break

    if salary_component is None and len(components) == 1:
        component = components[0]
        if component.get("minValue") is not None or component.get("maxValue") is not None:
            salary_component = component

    fields = {}
    printed_salary = (
        compensation.get("scrapeableCompensationSalarySummary")
        or compensation.get("compensationTierSummary")
    )
    if printed_salary:
        fields["salary"] = printed_salary

    if salary_component:
        if salary_component.get("minValue") is not None:
            fields["salary_min"] = salary_component["minValue"]
        if salary_component.get("maxValue") is not None:
            fields["salary_max"] = salary_component["maxValue"]
        if salary_component.get("currencyCode"):
            fields["salary_currency"] = salary_component["currencyCode"]
        if salary_component.get("interval"):
            fields["salary_period"] = salary_component["interval"]

    return fields


def crawl(ctx):
    response = ctx.fetch(SITE)
    payload = json.loads(response)

    for posting in payload.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        title = posting.get("title")
        description = posting.get("descriptionPlain")
        location = posting.get("location")
        job_url = posting.get("jobUrl")
        if not title or not description or not location or not job_url:
            continue

        locations = [location]
        for secondary in posting.get("secondaryLocations") or []:
            secondary_location = secondary.get("location")
            if secondary_location and secondary_location not in locations:
                locations.append(secondary_location)

        fields = {
            "url": job_url,
            "title": title,
            "company": "Sanity",
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
        fields.update({key: value for key, value in optional_fields.items() if value})
        fields.update(_salary_fields(posting))

        yield fields
