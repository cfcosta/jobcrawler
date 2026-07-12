
import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/Roboflow?includeCompensation=true"
EXTRA_HOSTS = []


def _salary_fields(job):
    compensation = job.get("compensation") or {}
    components = compensation.get("summaryComponents") or []

    salary_components = [
        component
        for component in components
        if component.get("compensationType") == "Salary"
    ]
    if not salary_components:
        return {}

    currencies = {
        component.get("currencyCode")
        for component in salary_components
        if component.get("currencyCode")
    }
    intervals = {
        component.get("interval")
        for component in salary_components
        if component.get("interval")
    }
    if len(currencies) > 1 or len(intervals) > 1:
        return {}

    component = salary_components[0]
    fields = {}

    printed = compensation.get("scrapeableCompensationSalarySummary")
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
    response = ctx.fetch(SITE)
    data = json.loads(response)

    for posting in data.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        title = posting.get("title")
        location = posting.get("location")
        description = posting.get("descriptionPlain")
        url = posting.get("jobUrl")
        if not title or not location or not description or not url:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Roboflow",
            "location": location,
            "description": description,
            "source": SITE,
        }

        secondary = posting.get("secondaryLocations") or []
        if secondary:
            locations = [location]
            for item in secondary:
                value = item.get("location") if isinstance(item, dict) else item
                if value:
                    locations.append(value)
            fields["locations"] = locations

        optional = {
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department") or posting.get("team"),
            "posted_at": posting.get("publishedAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        fields.update({key: value for key, value in optional.items() if value})
        fields.update(_salary_fields(posting))

        yield fields
