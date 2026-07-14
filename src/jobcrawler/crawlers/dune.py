
import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/dune?includeCompensation=true"
EXTRA_HOSTS = []


def _set_if_present(data, key, value):
    if value is not None and value != "":
        data[key] = value


def _compensation_fields(compensation):
    result = {}
    if not isinstance(compensation, dict):
        return result

    printed = (
        compensation.get("compensationTierSummary")
        or compensation.get("scrapeableCompensationSalarySummary")
    )
    _set_if_present(result, "salary", printed)

    components = compensation.get("summaryComponents") or []
    salary_components = [
        component
        for component in components
        if isinstance(component, dict)
        and (
            component.get("minValue") is not None
            or component.get("maxValue") is not None
        )
    ]
    if not salary_components:
        return result

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

    # A single Job salary quartet cannot faithfully represent mixed units.
    if len(currencies) > 1 or len(intervals) > 1:
        return result

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
    if minimums:
        result["salary_min"] = min(minimums)
    if maximums:
        result["salary_max"] = max(maximums)
    if currencies:
        result["salary_currency"] = next(iter(currencies))
    if intervals:
        result["salary_period"] = next(iter(intervals))
    return result


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))
    seen = set()

    for posting in payload.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        external_id = posting.get("id")
        if external_id in seen:
            continue
        seen.add(external_id)

        primary_location = posting.get("location")
        locations = []
        if primary_location:
            locations.append(primary_location)
        for secondary in posting.get("secondaryLocations") or []:
            location = secondary.get("location") if isinstance(secondary, dict) else None
            if location and location not in locations:
                locations.append(location)

        data = {
            "url": posting.get("jobUrl"),
            "title": posting.get("title"),
            "company": "Dune",
            "location": primary_location,
            "source": SITE,
            "description": posting.get("descriptionPlain"),
        }
        if locations:
            data["locations"] = locations

        _set_if_present(data, "department", posting.get("department"))
        _set_if_present(data, "employment_type", posting.get("employmentType"))
        _set_if_present(data, "workplace_type", posting.get("workplaceType"))
        _set_if_present(data, "posted_at", posting.get("publishedAt"))
        _set_if_present(data, "external_id", external_id)
        _set_if_present(data, "apply_url", posting.get("applyUrl"))
        data.update(_compensation_fields(posting.get("compensation")))

        yield data
