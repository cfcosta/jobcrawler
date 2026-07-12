import json


SITE = 'https://api.ashbyhq.com/posting-api/job-board/Tools%20for%20Humanity?includeCompensation=true'
EXTRA_HOSTS = []


def _salary_fields(job):
    compensation = job.get("compensation") or {}
    fields = {}

    printed = (
        compensation.get("compensationTierSummary")
        or compensation.get("scrapeableCompensationSalarySummary")
    )
    if printed:
        fields["salary"] = printed

    components = compensation.get("summaryComponents") or []
    numeric_components = [
        item for item in components
        if item.get("minValue") is not None
        or item.get("maxValue") is not None
    ]
    component = next(
        (
            item for item in numeric_components
            if str(item.get("compensationType", "")).lower() == "salary"
        ),
        numeric_components[0] if numeric_components else None,
    )
    if component is None:
        return fields

    mapping = (
        ("minValue", "salary_min"),
        ("maxValue", "salary_max"),
        ("currencyCode", "salary_currency"),
        ("interval", "salary_period"),
    )
    for source_key, job_key in mapping:
        value = component.get(source_key)
        if value is not None and value != "":
            fields[job_key] = value

    return fields


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for posting in payload.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        fields = {
            "url": posting["jobUrl"],
            "title": posting["title"],
            "company": "Tools for Humanity",
            "location": posting["location"],
            "description": posting["descriptionPlain"],
            "source": SITE,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
        }

        secondary = posting.get("secondaryLocations") or []
        if secondary:
            locations = [posting["location"]]
            locations.extend(
                item.get("location")
                for item in secondary
                if item.get("location")
            )
            fields["locations"] = locations

        fields.update(_salary_fields(posting))
        yield {
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        }
