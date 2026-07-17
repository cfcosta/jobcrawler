
import json
from jobcrawler.runtime.contract import Job

SITE = "https://api.ashbyhq.com/posting-api/job-board/slab?includeCompensation=true"
EXTRA_HOSTS = []


def _text(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _named_value(value):
    if isinstance(value, dict):
        return _text(value.get("name") or value.get("location"))
    return _text(value)


def _compensation(compensation):
    if not isinstance(compensation, dict):
        return {}

    result = {}
    printed = _text(
        compensation.get("scrapeableCompensationSalarySummary")
        or compensation.get("compensationTierSummary")
    )
    if printed is not None:
        result["salary"] = printed

    components = compensation.get("summaryComponents")
    if not isinstance(components, list):
        return result

    salary_components = [
        component
        for component in components
        if isinstance(component, dict)
        and _text(component.get("compensationType"))
        and _text(component.get("compensationType")).lower() == "salary"
    ]
    if not salary_components:
        salary_components = [
            component
            for component in components
            if isinstance(component, dict)
            and (
                component.get("minValue") is not None
                or component.get("maxValue") is not None
            )
        ]

    currencies = {
        _text(component.get("currencyCode"))
        for component in salary_components
        if _text(component.get("currencyCode")) is not None
    }
    if len(currencies) > 1 or not salary_components:
        return result

    component = salary_components[0]
    if component.get("minValue") is not None:
        result["salary_min"] = component["minValue"]
    if component.get("maxValue") is not None:
        result["salary_max"] = component["maxValue"]

    currency = _text(component.get("currencyCode"))
    period = _text(component.get("interval"))
    if currency is not None:
        result["salary_currency"] = currency
    if period is not None:
        result["salary_period"] = period

    return result


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))
    jobs = payload.get("jobs", [])
    if not isinstance(jobs, list):
        return

    for item in jobs:
        if not isinstance(item, dict):
            continue

        url = _text(item.get("jobUrl"))
        title = _text(item.get("title"))
        location = _named_value(item.get("location"))
        description = _text(item.get("descriptionPlain"))
        if not all((url, title, location, description)):
            continue

        values = {
            "url": url,
            "title": title,
            "company": "Slab",
            "location": location,
            "description": description,
            "source": SITE,
        }

        locations = [location]
        secondary = item.get("secondaryLocations")
        if isinstance(secondary, list):
            for entry in secondary:
                value = _named_value(entry)
                if value is not None:
                    locations.append(value)
        values["locations"] = locations

        optional_fields = (
            ("employmentType", "employment_type"),
            ("workplaceType", "workplace_type"),
            ("publishedAt", "posted_at"),
            ("expiresAt", "expires_at"),
            ("id", "external_id"),
            ("applyUrl", "apply_url"),
        )
        for source_key, target_key in optional_fields:
            value = _text(item.get(source_key))
            if value is not None:
                values[target_key] = value

        if "workplace_type" not in values and item.get("isRemote") is True:
            values["workplace_type"] = "Remote"

        department = _named_value(item.get("department"))
        if department is not None:
            values["department"] = department

        values.update(_compensation(item.get("compensation")))
        yield Job(**values)
