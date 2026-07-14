import json


SITE = 'https://api.ashbyhq.com/posting-api/job-board/sublime-security?includeCompensation=true'
EXTRA_HOSTS = []

COMPANY = "Sublime Security"


def _present(value):
    return value is not None and value != ""


def _location_name(value):
    if isinstance(value, str):
        return value if value else None
    if isinstance(value, dict):
        value = value.get("location")
        return value if isinstance(value, str) and value else None
    return None


def _add_compensation(result, compensation):
    if not isinstance(compensation, dict):
        return

    printed = (
        compensation.get("compensationTierSummary")
        or compensation.get("scrapeableCompensationSalarySummary")
        or compensation.get("summary")
    )
    if _present(printed):
        result["salary"] = printed

    components = compensation.get("summaryComponents")
    if not isinstance(components, list):
        return

    salary_components = []
    for component in components:
        if not isinstance(component, dict):
            continue
        kind = str(component.get("compensationType") or "").lower()
        if kind and "salary" not in kind and "base" not in kind:
            continue
        if _present(component.get("minValue")) or _present(component.get("maxValue")):
            salary_components.append(component)

    if not salary_components:
        return

    currencies = {
        component.get("currencyCode")
        for component in salary_components
        if _present(component.get("currencyCode"))
    }
    periods = {
        component.get("interval")
        for component in salary_components
        if _present(component.get("interval"))
    }

    # Combining ranges is safe only when all components describe one unit.
    if len(currencies) > 1 or len(periods) > 1:
        return

    minimums = [
        component["minValue"]
        for component in salary_components
        if _present(component.get("minValue"))
    ]
    maximums = [
        component["maxValue"]
        for component in salary_components
        if _present(component.get("maxValue"))
    ]

    if minimums:
        result["salary_min"] = min(minimums)
    if maximums:
        result["salary_max"] = max(maximums)
    if currencies:
        result["salary_currency"] = next(iter(currencies))
    if periods:
        result["salary_period"] = next(iter(periods))


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs", []):
        url = posting.get("jobUrl")
        title = posting.get("title")
        location = posting.get("location")
        description = posting.get("descriptionPlain")

        if not all(_present(value) for value in (url, title, location, description)):
            continue

        result = {
            "url": url,
            "title": title,
            "company": COMPANY,
            "location": location,
            "description": description,
            "source": "api",
        }

        secondary = posting.get("secondaryLocations")
        if isinstance(secondary, list):
            locations = [location]
            for entry in secondary:
                name = _location_name(entry)
                if name:
                    locations.append(name)
            result["locations"] = locations

        fields = (
            ("employmentType", "employment_type"),
            ("workplaceType", "workplace_type"),
            ("department", "department"),
            ("publishedAt", "posted_at"),
            ("expiresAt", "expires_at"),
            ("id", "external_id"),
            ("applyUrl", "apply_url"),
        )
        for source_key, result_key in fields:
            value = posting.get(source_key)
            if _present(value):
                result[result_key] = value

        _add_compensation(result, posting.get("compensation"))
        yield result
