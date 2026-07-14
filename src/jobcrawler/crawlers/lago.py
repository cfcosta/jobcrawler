
import json


SITE = "https://api.ashbyhq.com/posting-api/job-board/lago?includeCompensation=true"
EXTRA_HOSTS = []


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    seen = set()

    for item in data.get("jobs", []):
        if item.get("isListed") is False:
            continue

        identity = item.get("id") or item.get("jobUrl")
        if not identity or identity in seen:
            continue
        seen.add(identity)

        description = item.get("descriptionPlain")
        if not description:
            continue

        values = {
            "source": SITE,
            "url": item.get("jobUrl"),
            "title": item.get("title"),
            "company": "Lago",
            "location": item.get("location"),
            "description": description,
            "external_id": item.get("id"),
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "department": item.get("department") or item.get("team"),
            "posted_at": item.get("publishedAt"),
            "apply_url": item.get("applyUrl"),
        }

        locations = []
        if item.get("location"):
            locations.append(item["location"])
        for secondary in item.get("secondaryLocations") or []:
            secondary_location = secondary.get("location")
            if secondary_location and secondary_location not in locations:
                locations.append(secondary_location)
        if locations:
            values["locations"] = locations

        compensation = item.get("compensation") or {}
        printed_salary = (
            compensation.get("compensationTierSummary")
            or compensation.get("scrapeableCompensationSalarySummary")
        )
        if printed_salary:
            values["salary"] = printed_salary

        salary_components = [
            component
            for component in compensation.get("summaryComponents") or []
            if component.get("compensationType") == "Salary"
            and component.get("currencyCode")
            and (
                component.get("minValue") is not None
                or component.get("maxValue") is not None
            )
        ]
        currencies = {
            component["currencyCode"] for component in salary_components
        }
        if len(currencies) == 1 and salary_components:
            component = salary_components[0]
            values["salary_currency"] = component["currencyCode"]
            if component.get("minValue") is not None:
                values["salary_min"] = component["minValue"]
            if component.get("maxValue") is not None:
                values["salary_max"] = component["maxValue"]
            if component.get("interval"):
                values["salary_period"] = component["interval"]

        yield {
            field: value
            for field, value in values.items()
            if value is not None and value != ""
        }
