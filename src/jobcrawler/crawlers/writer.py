
import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/writer?includeCompensation=true'
EXTRA_HOSTS = []


def _set_if_present(result, key, value):
    if value is not None and value != "":
        result[key] = value


def _salary_fields(result, compensation):
    if not isinstance(compensation, dict):
        return

    components = [
        component
        for component in compensation.get("summaryComponents", [])
        if component.get("compensationType") == "Salary"
    ]
    if len(components) != 1:
        return

    component = components[0]
    currencies = {
        item.get("currencyCode")
        for item in components
        if item.get("currencyCode")
    }
    if len(currencies) > 1:
        return

    _set_if_present(
        result,
        "salary",
        compensation.get("scrapeableCompensationSalarySummary")
        or compensation.get("compensationTierSummary"),
    )
    _set_if_present(result, "salary_min", component.get("minValue"))
    _set_if_present(result, "salary_max", component.get("maxValue"))
    _set_if_present(result, "salary_currency", component.get("currencyCode"))
    _set_if_present(result, "salary_period", component.get("interval"))


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for item in payload.get("jobs", []):
        if item.get("isListed") is not True:
            continue

        title = item.get("title")
        location = item.get("location")
        description = item.get("descriptionPlain")
        url = item.get("jobUrl")
        if not title or not location or not description or not url:
            continue

        result = {
            "url": url,
            "title": title,
            "company": "WRITER",
            "location": location,
            "description": description,
            "source": SITE,
        }

        locations = [location]
        for secondary in item.get("secondaryLocations") or []:
            secondary_location = secondary.get("location")
            if secondary_location and secondary_location not in locations:
                locations.append(secondary_location)
        result["locations"] = locations

        _set_if_present(result, "external_id", item.get("id"))
        _set_if_present(result, "apply_url", item.get("applyUrl"))
        _set_if_present(result, "employment_type", item.get("employmentType"))
        _set_if_present(result, "department", item.get("department"))
        _set_if_present(result, "posted_at", item.get("publishedAt"))

        workplace_type = item.get("workplaceType")
        if workplace_type:
            result["workplace_type"] = workplace_type
        elif item.get("isRemote") is True:
            result["workplace_type"] = "Remote"

        _salary_fields(result, item.get("compensation"))
        yield result
