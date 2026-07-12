
import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/lambda?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    response = ctx.fetch(SITE)
    if hasattr(response, "json"):
        payload = response.json()
    else:
        text = response.text if hasattr(response, "text") else response
        payload = json.loads(text)

    for item in payload.get("jobs", []):
        if item.get("isListed") is False:
            continue

        primary = item.get("location")
        locations = []
        if primary:
            locations.append(primary)
        for secondary in item.get("secondaryLocations") or []:
            value = secondary.get("location")
            if value and value not in locations:
                locations.append(value)

        values = {
            "url": item.get("jobUrl"),
            "title": item.get("title"),
            "company": "Lambda",
            "location": primary,
            "locations": locations or None,
            "description": item.get("descriptionPlain"),
            "source": SITE,
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "department": item.get("department"),
            "posted_at": item.get("publishedAt"),
            "external_id": item.get("id"),
            "apply_url": item.get("applyUrl"),
        }

        compensation = item.get("compensation") or {}
        components = [
            component
            for component in compensation.get("summaryComponents") or []
            if component.get("compensationType") == "Salary"
        ]
        if len(components) == 1:
            component = components[0]
            if (
                component.get("minValue") is not None
                and component.get("maxValue") is not None
                and component.get("currencyCode")
                and component.get("interval")
            ):
                values.update({
                    "salary": compensation.get("compensationTierSummary"),
                    "salary_min": component["minValue"],
                    "salary_max": component["maxValue"],
                    "salary_currency": component["currencyCode"],
                    "salary_period": component["interval"],
                })

        yield {key: value for key, value in values.items() if value is not None}
