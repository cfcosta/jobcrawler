
import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/vellum?includeCompensation=true"
EXTRA_HOSTS = []


def _compensation_fields(compensation):
    compensation = compensation or {}
    result = {}

    printed = compensation.get("scrapeableCompensationSalarySummary")
    if printed:
        result["salary"] = printed

    for component in compensation.get("summaryComponents") or []:
        if not isinstance(component, dict):
            continue

        minimum = component.get("minValue")
        maximum = component.get("maxValue")
        currency = component.get("currencyCode")
        period = component.get("interval")

        if minimum is not None:
            result["salary_min"] = minimum
        if maximum is not None:
            result["salary_max"] = maximum
        if currency:
            result["salary_currency"] = currency
        if period:
            result["salary_period"] = period

        if any(value is not None for value in (minimum, maximum, currency, period)):
            break

    return result


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for posting in payload.get("jobs", []):
        if not posting.get("isListed", True):
            continue

        location = posting.get("location")
        locations = []
        if location:
            locations.append(location)

        for secondary in posting.get("secondaryLocations") or []:
            value = secondary.get("location") if isinstance(secondary, dict) else secondary
            if value:
                locations.append(value)

        fields = {
            "url": posting.get("jobUrl"),
            "title": posting.get("title"),
            "company": "Vellum",
            "location": location,
            "description": posting.get("descriptionPlain"),
            "source": SITE,
            "external_id": posting.get("id"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
            "apply_url": posting.get("applyUrl"),
        }
        if locations:
            fields["locations"] = locations

        fields.update(_compensation_fields(posting.get("compensation")))
        yield {key: value for key, value in fields.items() if value is not None}
