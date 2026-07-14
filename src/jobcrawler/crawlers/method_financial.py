import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/method?includeCompensation=true"
EXTRA_HOSTS = []


def _compensation_fields(item):
    compensation = item.get("compensation") or {}
    components = compensation.get("summaryComponents") or []
    salary_component = next(
        (
            component
            for component in components
            if component.get("minValue") is not None
            or component.get("maxValue") is not None
        ),
        None,
    )

    fields = {}
    if salary_component:
        mappings = (
            ("minValue", "salary_min"),
            ("maxValue", "salary_max"),
            ("currencyCode", "salary_currency"),
            ("interval", "salary_period"),
        )
        for source_name, job_name in mappings:
            value = salary_component.get(source_name)
            if value is not None and value != "":
                fields[job_name] = value

    printed = compensation.get("compensationTierSummary")
    if not isinstance(printed, str) or not printed.strip():
        printed = compensation.get("scrapeableCompensationSalarySummary")
    if isinstance(printed, str) and printed.strip():
        fields["salary"] = printed

    return fields


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for item in payload.get("jobs", []):
        if item.get("isListed") is False:
            continue

        fields = {
            "url": item["jobUrl"],
            "title": item["title"].strip(),
            "company": "Method",
            "location": item["location"],
            "description": item["descriptionPlain"],
            "source": SITE,
        }

        secondary_locations = [
            entry["location"]
            for entry in (item.get("secondaryLocations") or [])
            if entry.get("location")
        ]
        fields["locations"] = [item["location"]] + secondary_locations

        optional_fields = (
            ("employment_type", item.get("employmentType")),
            ("department", item.get("department")),
            ("posted_at", item.get("publishedAt")),
            ("external_id", item.get("id")),
            ("apply_url", item.get("applyUrl")),
        )
        for name, value in optional_fields:
            if value is not None and value != "":
                fields[name] = value

        workplace_type = item.get("workplaceType")
        if not workplace_type and item.get("isRemote") is True:
            workplace_type = "Remote"
        if workplace_type:
            fields["workplace_type"] = workplace_type

        fields.update(_compensation_fields(item))
        yield fields
