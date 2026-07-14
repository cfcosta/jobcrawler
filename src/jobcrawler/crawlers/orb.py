
import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/orb?includeCompensation=true"
EXTRA_HOSTS = []


def _salary_fields(posting):
    compensation = posting.get("compensation") or {}
    components = compensation.get("summaryComponents") or []
    salary_components = [
        component
        for component in components
        if component.get("compensationType") == "Salary"
        and (
            component.get("minValue") is not None
            or component.get("maxValue") is not None
        )
    ]
    if not salary_components:
        return {}

    currencies = {
        component.get("currencyCode")
        for component in salary_components
        if component.get("currencyCode")
    }
    periods = {
        component.get("interval")
        for component in salary_components
        if component.get("interval")
    }
    if len(currencies) > 1 or len(periods) > 1:
        return {}

    minimums = [
        component["minValue"]
        for component in salary_components
        if component.get("minValue") is not None
    ]
    maximums = [
        component["maxValue"]
        for component in salary_components
        if component.get("maxValue") is not None
    ]

    fields = {}
    printed = compensation.get("scrapeableCompensationSalarySummary")
    if printed:
        fields["salary"] = printed
    if minimums:
        fields["salary_min"] = min(minimums)
    if maximums:
        fields["salary_max"] = max(maximums)
    if currencies:
        fields["salary_currency"] = next(iter(currencies))
    if periods:
        fields["salary_period"] = next(iter(periods))
    return fields


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for posting in payload.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        description = posting.get("descriptionPlain")
        if not description:
            continue

        values = {
            "url": posting.get("jobUrl"),
            "title": posting.get("title"),
            "company": "Orb",
            "location": posting.get("location"),
            "description": description,
            "source": SITE,
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department") or posting.get("team"),
            "posted_at": posting.get("publishedAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }

        secondary = posting.get("secondaryLocations") or []
        if secondary:
            locations = []
            if posting.get("location"):
                locations.append(posting["location"])
            for item in secondary:
                location = item.get("location") if isinstance(item, dict) else item
                if location:
                    locations.append(location)
            if locations:
                values["locations"] = locations

        values.update(_salary_fields(posting))
        yield {
            key: value
            for key, value in values.items()
            if value is not None and value != ""
        }
