
import json


SITE = "https://api.ashbyhq.com/posting-api/job-board/cantina?includeCompensation=true"
EXTRA_HOSTS = []


def _compensation_fields(posting):
    compensation = posting.get("compensation") or {}
    fields = {}

    printed = compensation.get("scrapeableCompensationSalarySummary")
    if isinstance(printed, str) and printed.strip():
        fields["salary"] = printed.strip()

    components = compensation.get("summaryComponents") or []
    components = [
        component for component in components
        if isinstance(component, dict)
        and (component.get("minValue") is not None or component.get("maxValue") is not None)
    ]
    currencies = {
        component.get("currencyCode")
        for component in components
        if component.get("currencyCode")
    }

    if components and len(currencies) <= 1:
        component = components[0]
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
    response = ctx.fetch(SITE)
    payload = json.loads(response)

    for posting in payload.get("jobs", []):
        title = posting.get("title")
        url = posting.get("jobUrl")
        location = posting.get("location")
        description = posting.get("descriptionPlain")
        if not title or not url or not location or not description:
            continue

        locations = [location]
        for secondary in posting.get("secondaryLocations") or []:
            if isinstance(secondary, dict) and secondary.get("location"):
                locations.append(secondary["location"])

        fields = {
            "url": url,
            "title": title,
            "company": "Cantina",
            "location": location,
            "locations": locations,
            "description": description.strip(),
            "source": SITE,
        }

        optional = {
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department") or posting.get("team"),
            "posted_at": posting.get("publishedAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        fields.update({
            key: value
            for key, value in optional.items()
            if value is not None and value != ""
        })
        fields.update(_compensation_fields(posting))
        yield fields
