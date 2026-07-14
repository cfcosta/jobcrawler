import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/artemisanalytics?includeCompensation=true'
EXTRA_HOSTS = []


def _salary_fields(item):
    compensation = item.get("compensation") or {}
    components = compensation.get("summaryComponents") or []
    salary_components = [
        component for component in components
        if component.get("compensationType") == "Salary"
    ]
    if len(salary_components) != 1:
        return {}

    component = salary_components[0]
    result = {}
    field_map = {
        "minValue": "salary_min",
        "maxValue": "salary_max",
        "currencyCode": "salary_currency",
        "interval": "salary_period",
    }
    for source_key, job_key in field_map.items():
        value = component.get(source_key)
        if value is not None and value != "":
            result[job_key] = value

    printed = compensation.get("compensationTierSummary")
    if printed:
        result["salary"] = printed
    return result


def crawl(ctx):
    raw = ctx.fetch(SITE)
    if hasattr(raw, "text"):
        raw = raw.text
    payload = json.loads(raw)

    for item in payload.get("jobs", []):
        if not item.get("isListed"):
            continue

        job = {
            "url": item["jobUrl"],
            "title": item["title"],
            "company": "Artemis Analytics",
            "location": item["location"],
            "description": item["descriptionPlain"],
            "source": SITE,
        }

        optional_fields = {
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "department": item.get("department") or item.get("team"),
            "posted_at": item.get("publishedAt"),
            "external_id": item.get("id"),
            "apply_url": item.get("applyUrl"),
        }
        job.update({
            key: value for key, value in optional_fields.items()
            if value is not None and value != ""
        })

        locations = []
        if item.get("location"):
            locations.append(item["location"])
        for secondary in item.get("secondaryLocations") or []:
            value = secondary.get("location") if isinstance(secondary, dict) else secondary
            if value:
                locations.append(value)
        if locations:
            job["locations"] = locations

        job.update(_salary_fields(item))
        yield job
