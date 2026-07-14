import json



SITE = "https://api.ashbyhq.com/posting-api/job-board/risklabs?includeCompensation=true"
EXTRA_HOSTS = []


def _present(value):
    return value is not None and value != ""


def _compensation_fields(compensation):
    if not isinstance(compensation, dict):
        return {}

    result = {}
    printed = (
        compensation.get("compensationTierSummary")
        or compensation.get("scrapeableCompensationSalarySummary")
    )
    if _present(printed):
        result["salary"] = printed

    components = compensation.get("summaryComponents") or []
    salary_components = [
        component
        for component in components
        if isinstance(component, dict)
        and (
            _present(component.get("minValue"))
            or _present(component.get("maxValue"))
        )
    ]
    if not salary_components:
        return result

    currencies = {
        component.get("currencyCode")
        for component in salary_components
        if _present(component.get("currencyCode"))
    }
    intervals = {
        component.get("interval")
        for component in salary_components
        if _present(component.get("interval"))
    }

    # A single structured range can be represented without merging unlike units.
    if len(salary_components) == 1 and len(currencies) <= 1 and len(intervals) <= 1:
        component = salary_components[0]
        mappings = (
            ("minValue", "salary_min"),
            ("maxValue", "salary_max"),
            ("currencyCode", "salary_currency"),
            ("interval", "salary_period"),
        )
        for source_key, destination_key in mappings:
            value = component.get(source_key)
            if _present(value):
                result[destination_key] = value

    return result


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for posting in payload.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        title = posting.get("title")
        location = posting.get("location")
        description = posting.get("descriptionPlain")
        url = posting.get("jobUrl")
        if not all(_present(value) for value in (title, location, description, url)):
            continue

        locations = [location]
        for secondary in posting.get("secondaryLocations") or []:
            if isinstance(secondary, dict):
                secondary_location = secondary.get("location")
                if _present(secondary_location):
                    locations.append(secondary_location)

        data = {
            "title": title,
            "company": "Risk Labs",
            "location": location,
            "locations": locations,
            "description": description,
            "source": SITE,
        }

        optional_fields = (
            ("employmentType", "employment_type"),
            ("workplaceType", "workplace_type"),
            ("department", "department"),
            ("publishedAt", "posted_at"),
            ("id", "external_id"),
            ("applyUrl", "apply_url"),
        )
        for source_key, destination_key in optional_fields:
            value = posting.get(source_key)
            if _present(value):
                data[destination_key] = value

        data.update(_compensation_fields(posting.get("compensation")))
        data["url"] = url
        yield data
