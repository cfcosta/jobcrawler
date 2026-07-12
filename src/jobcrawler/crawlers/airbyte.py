import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/airbyte?includeCompensation=true'
EXTRA_HOSTS = []


def _salary_fields(job):
    compensation = job.get("compensation") or {}
    components = compensation.get("summaryComponents") or []
    salary_components = [
        component for component in components
        if component.get("compensationType") == "Salary"
        and component.get("currencyCode")
        and component.get("minValue") is not None
        and component.get("maxValue") is not None
    ]

    currencies = {component["currencyCode"] for component in salary_components}
    if not salary_components or len(currencies) != 1:
        return {}

    fields = {
        "salary_min": min(component["minValue"] for component in salary_components),
        "salary_max": max(component["maxValue"] for component in salary_components),
        "salary_currency": next(iter(currencies)),
    }

    periods = {component.get("interval") for component in salary_components}
    if len(periods) == 1 and None not in periods:
        fields["salary_period"] = next(iter(periods))

    printed = compensation.get("scrapeableCompensationSalarySummary")
    if printed:
        fields["salary"] = printed

    return fields


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for item in data.get("jobs", []):
        if item.get("isListed") is not True:
            continue

        description = item.get("descriptionPlain")
        if not description:
            continue

        location = item.get("location")
        fields = {
            "source": SITE,
            "url": item["jobUrl"],
            "title": item["title"],
            "company": "Airbyte",
            "location": location,
            "description": description,
            "external_id": item.get("id"),
            "apply_url": item.get("applyUrl"),
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "department": item.get("department") or item.get("team"),
            "posted_at": item.get("publishedAt"),
        }

        secondary = item.get("secondaryLocations") or []
        locations = ([location] if location else []) + [
            entry.get("location")
            for entry in secondary
            if isinstance(entry, dict) and entry.get("location")
        ]
        if locations:
            fields["locations"] = locations

        fields.update(_salary_fields(item))
        yield {
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        }
