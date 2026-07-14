
import json

SITE = "https://jobs.ashbyhq.com/uniswap"
EXTRA_HOSTS = ["api.ashbyhq.com"]
API_URL = "https://api.ashbyhq.com/posting-api/job-board/uniswap?includeCompensation=true"


def _location_value(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("location") or value.get("name")
    return None


def _compensation_fields(posting):
    compensation = posting.get("compensation") or {}
    result = {}

    printed = (
        compensation.get("compensationTierSummary")
        or compensation.get("summary")
        or posting.get("compensationTierSummary")
    )
    if printed:
        result["salary"] = printed

    components = compensation.get("summaryComponents") or []
    if isinstance(components, dict):
        components = [components]

    numeric = []
    for component in components:
        if not isinstance(component, dict):
            continue
        minimum = component.get("minValue")
        maximum = component.get("maxValue")
        currency = component.get("currencyCode")
        if minimum is not None and maximum is not None and currency:
            numeric.append(component)

    currencies = {component.get("currencyCode") for component in numeric}
    if numeric and len(currencies) == 1:
        minimums = [component["minValue"] for component in numeric]
        maximums = [component["maxValue"] for component in numeric]
        periods = {
            component.get("interval")
            for component in numeric
            if component.get("interval")
        }

        result["salary_min"] = min(minimums)
        result["salary_max"] = max(maximums)
        result["salary_currency"] = next(iter(currencies))
        if len(periods) == 1:
            result["salary_period"] = next(iter(periods))

    return result


def crawl(ctx):
    payload = json.loads(ctx.fetch(API_URL))

    for posting in payload.get("jobs") or []:
        description = (
            posting.get("descriptionPlain")
            or posting.get("descriptionHtml")
        )
        title = posting.get("title")
        posting_id = posting.get("id")
        url = (
            posting.get("jobUrl")
            or posting.get("url")
            or (
                "https://jobs.ashbyhq.com/uniswap/" + posting_id
                if posting_id else None
            )
        )
        primary_location = _location_value(posting.get("location"))

        if not title or not description or not url or not primary_location:
            continue

        locations = [primary_location]
        for secondary in posting.get("secondaryLocations") or []:
            value = _location_value(secondary)
            if value and value not in locations:
                locations.append(value)

        fields = {
            "url": url,
            "title": title,
            "company": "Uniswap Labs",
            "location": primary_location,
            "locations": locations,
            "description": description,
            "source": SITE,
            "external_id": posting_id,
            "apply_url": posting.get("applyUrl"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department"),
            "posted_at": (
                posting.get("publishedAt")
                or posting.get("publishedDate")
            ),
            "expires_at": posting.get("applicationDeadline"),
        }
        fields.update(_compensation_fields(posting))

        yield {
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        }
