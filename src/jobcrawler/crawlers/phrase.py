import json


SITE = "https://api.ashbyhq.com/posting-api/job-board/phrase?includeCompensation=true"
EXTRA_HOSTS = []


def _present(value):
    return value is not None and value != ""


def _compensation_fields(compensation):
    if not isinstance(compensation, dict):
        return {}

    components = compensation.get("summaryComponents") or []
    salary_components = [
        component
        for component in components
        if isinstance(component, dict)
        and (_present(component.get("minValue")) or _present(component.get("maxValue")))
    ]
    if len(salary_components) != 1:
        return {}

    component = salary_components[0]
    fields = {}
    if _present(component.get("minValue")):
        fields["salary_min"] = component["minValue"]
    if _present(component.get("maxValue")):
        fields["salary_max"] = component["maxValue"]
    if _present(component.get("currencyCode")):
        fields["salary_currency"] = component["currencyCode"]
    if _present(component.get("interval")):
        fields["salary_period"] = component["interval"]

    printed = compensation.get("compensationTierSummary")
    if not _present(printed):
        printed = compensation.get("scrapeableCompensationSalarySummary")
    if _present(printed):
        fields["salary"] = printed
    return fields


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        location = posting.get("location")
        locations = []
        if _present(location):
            locations.append(location)
        for secondary in posting.get("secondaryLocations") or []:
            if isinstance(secondary, dict) and _present(secondary.get("location")):
                locations.append(secondary["location"])

        workplace_type = posting.get("workplaceType")
        if not _present(workplace_type):
            if posting.get("isRemote") is True:
                workplace_type = "Remote"
            elif posting.get("isRemote") is False:
                workplace_type = "On-site"

        fields = {
            "url": posting.get("jobUrl"),
            "source": SITE,
            "title": posting.get("title"),
            "company": "Phrase",
            "location": location,
            "description": posting.get("descriptionPlain"),
            "external_id": posting.get("id"),
            "employment_type": posting.get("employmentType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
            "apply_url": posting.get("applyUrl"),
            "workplace_type": workplace_type,
        }
        if locations:
            fields["locations"] = locations
        fields.update(_compensation_fields(posting.get("compensation")))

        yield {
            key: value
            for key, value in fields.items()
            if _present(value)
        }
