import json
SITE = 'https://api.ashbyhq.com/posting-api/job-board/filigran?includeCompensation=true'
EXTRA_HOSTS = []


def _present(value):
    return value is not None and value != ""


def _salary_fields(compensation):
    if not isinstance(compensation, dict):
        return {}

    result = {}
    printed = compensation.get("scrapeableCompensationSalarySummary")
    if _present(printed):
        result["salary"] = printed

    components = compensation.get("summaryComponents") or []
    component = next(
        (
            item for item in components
            if isinstance(item, dict)
            and any(_present(item.get(key)) for key in ("minValue", "maxValue"))
        ),
        None,
    )
    if component:
        mapping = {
            "minValue": "salary_min",
            "maxValue": "salary_max",
            "currencyCode": "salary_currency",
            "interval": "salary_period",
        }
        for source_key, job_key in mapping.items():
            value = component.get(source_key)
            if _present(value):
                result[job_key] = value
    return result


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))
    for posting in payload.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        primary_location = posting.get("location")
        locations = []
        if _present(primary_location):
            locations.append(primary_location)
        for secondary in posting.get("secondaryLocations") or []:
            if isinstance(secondary, dict):
                value = secondary.get("location")
                if _present(value):
                    locations.append(value)

        fields = {
            "url": posting.get("jobUrl"),
            "title": posting.get("title"),
            "company": "Filigran",
            "location": primary_location,
            "description": posting.get("descriptionPlain"),
            "source": SITE,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
        }
        if locations:
            fields["locations"] = locations
        fields.update(_salary_fields(posting.get("compensation")))
        yield {key: value for key, value in fields.items() if _present(value)}
