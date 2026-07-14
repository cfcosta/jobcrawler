import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/quantexa?includeCompensation=true"
EXTRA_HOSTS = []


def _compensation_fields(item):
    compensation = item.get("compensation") or {}
    salary_components = [
        component
        for component in compensation.get("summaryComponents") or []
        if component.get("compensationType") == "Salary"
    ]
    if len(salary_components) != 1:
        return {}

    component = salary_components[0]
    fields = {}
    printed = compensation.get("compensationTierSummary")
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
    seen = set()

    for item in payload.get("jobs", []):
        if not item.get("isListed", True):
            continue

        url = item.get("jobUrl")
        title = item.get("title")
        location = item.get("location")
        description = item.get("descriptionPlain")
        if not all((url, title, location, description)):
            continue

        identity = item.get("id") or url
        if identity in seen:
            continue
        seen.add(identity)

        job = {
            "url": url,
            "title": title,
            "company": "Quantexa",
            "location": location,
            "description": description,
            "source": SITE,
        }

        locations = [location]
        locations.extend(
            secondary["location"]
            for secondary in item.get("secondaryLocations") or []
            if secondary.get("location")
        )
        job["locations"] = locations

        optional = {
            "employment_type": item.get("employmentType"),
            "department": item.get("department"),
            "posted_at": item.get("publishedAt"),
            "external_id": item.get("id"),
            "apply_url": item.get("applyUrl"),
        }
        for key, value in optional.items():
            if value:
                job[key] = value

        workplace_type = item.get("workplaceType")
        if workplace_type:
            job["workplace_type"] = workplace_type
        elif item.get("isRemote") is True:
            job["workplace_type"] = "Remote"

        job.update(_compensation_fields(item))
        yield job
