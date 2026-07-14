import json


SITE = "https://api.ashbyhq.com/posting-api/job-board/atomic-financial?includeCompensation=true"
EXTRA_HOSTS = []


def _salary_fields(posting):
    compensation = posting.get("compensation") or {}
    components = [
        component
        for component in compensation.get("summaryComponents") or []
        if component.get("compensationType") == "Salary"
        and component.get("minValue") is not None
        and component.get("maxValue") is not None
        and component.get("currencyCode")
        and component.get("interval")
    ]
    if not components:
        return {}

    currencies = {component["currencyCode"] for component in components}
    periods = {component["interval"] for component in components}
    if len(currencies) != 1 or len(periods) != 1:
        return {}

    fields = {
        "salary_min": min(component["minValue"] for component in components),
        "salary_max": max(component["maxValue"] for component in components),
        "salary_currency": next(iter(currencies)),
        "salary_period": next(iter(periods)),
    }

    printed = compensation.get("scrapeableCompensationSalarySummary")
    if printed:
        fields["salary"] = printed

    return fields


def crawl(ctx):
    raw = ctx.fetch(SITE)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    payload = json.loads(raw)

    for posting in payload.get("jobs") or []:
        if posting.get("isListed") is False:
            continue

        title = posting.get("title")
        location = posting.get("location")
        description = posting.get("descriptionPlain")
        job_url = posting.get("jobUrl")
        if not title or not location or not description or not job_url:
            continue

        fields = {
            "url": job_url,
            "title": title.strip(),
            "company": "Atomic Financial",
            "location": location,
            "description": description,
            "source": SITE,
        }

        locations = [location]
        for secondary in posting.get("secondaryLocations") or []:
            value = secondary.get("location") if isinstance(secondary, dict) else secondary
            if value and value not in locations:
                locations.append(value)
        fields["locations"] = locations

        optional_fields = {
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department") or posting.get("team"),
            "posted_at": posting.get("publishedAt"),
        }
        fields.update({
            key: value
            for key, value in optional_fields.items()
            if value is not None and value != ""
        })
        fields.update(_salary_fields(posting))

        yield fields
