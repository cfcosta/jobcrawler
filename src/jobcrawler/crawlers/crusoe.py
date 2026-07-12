
import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/Crusoe?includeCompensation=true"
EXTRA_HOSTS = []


def _salary_fields(job):
    compensation = job.get("compensation") or {}
    components = compensation.get("summaryComponents") or []
    salary_components = [
        component
        for component in components
        if component.get("compensationType") == "Salary"
    ]

    if len(salary_components) != 1:
        return {}

    component = salary_components[0]
    result = {
        "salary": (
            compensation.get("compensationTierSummary")
            or compensation.get("scrapeableCompensationSalarySummary")
        ),
        "salary_min": component.get("minValue"),
        "salary_max": component.get("maxValue"),
        "salary_currency": component.get("currencyCode"),
        "salary_period": component.get("interval"),
    }
    return {
        key: value
        for key, value in result.items()
        if value is not None and value != ""
    }


def crawl(ctx):
    raw = ctx.fetch(SITE + "&_cb=crusoe-board-v1")
    payload = json.loads(raw)

    for posting in payload.get("jobs", []):
        if posting.get("isListed") is not True:
            continue

        primary_location = posting.get("location")
        locations = []
        if primary_location:
            locations.append(primary_location)

        for secondary in posting.get("secondaryLocations") or []:
            location = secondary.get("location")
            if location and location not in locations:
                locations.append(location)

        fields = {
            "url": posting.get("jobUrl"),
            "source": SITE,
            "title": posting.get("title"),
            "company": "Crusoe",
            "location": primary_location,
            "locations": locations or None,
            "description": posting.get("descriptionPlain"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        fields.update(_salary_fields(posting))

        yield {
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        }
