
import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/datafold?includeCompensation=true"
EXTRA_HOSTS = []


def salary_fields(item):
    compensation = item.get("compensation") or {}
    components = compensation.get("summaryComponents") or []
    salaries = []

    for component in components:
        if component.get("compensationType") != "Salary":
            continue
        if component.get("minValue") is None or component.get("maxValue") is None:
            continue
        if not component.get("currencyCode") or not component.get("interval"):
            continue
        salaries.append(component)

    if len(salaries) != 1:
        return {}

    component = salaries[0]
    result = {
        "salary_min": component["minValue"],
        "salary_max": component["maxValue"],
        "salary_currency": component["currencyCode"],
        "salary_period": component["interval"],
    }
    printed = compensation.get("scrapeableCompensationSalarySummary")
    if printed:
        result["salary"] = printed
    return result


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    seen = set()

    for item in data.get("jobs") or []:
        if item.get("isListed") is False:
            continue

        external_id = item.get("id")
        url = item.get("jobUrl")
        title = item.get("title")
        location = item.get("location")
        description = item.get("descriptionPlain")

        if not external_id or external_id in seen:
            continue
        if not url or not title or not location or not description:
            continue
        seen.add(external_id)

        locations = [location]
        for secondary in item.get("secondaryLocations") or []:
            if isinstance(secondary, dict):
                secondary_location = secondary.get("location")
            else:
                secondary_location = secondary
            if secondary_location and secondary_location not in locations:
                locations.append(secondary_location)

        fields = {
            "url": url,
            "title": title,
            "company": "Datafold",
            "location": location,
            "locations": locations,
            "description": description,
            "source": SITE,
            "external_id": external_id,
        }

        optional = {
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "department": item.get("department"),
            "posted_at": item.get("publishedAt"),
            "apply_url": item.get("applyUrl"),
        }
        for key, value in optional.items():
            if value is not None and value != "":
                fields[key] = value

        fields.update(salary_fields(item))
        yield fields
