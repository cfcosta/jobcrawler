
import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/langfuse?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for item in data.get("jobs", []):
        if item.get("isListed") is not True:
            continue

        description = item.get("descriptionPlain") or item.get("descriptionHtml")
        if not description:
            continue

        fields = {
            "url": item.get("jobUrl"),
            "title": item.get("title"),
            "company": "Langfuse",
            "location": item.get("location"),
            "description": description,
            "source": SITE,
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
            if secondary_location:
                locations.append(secondary_location)
        if locations:
            fields["locations"] = locations

        compensation = item.get("compensation") or {}
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

        if len(salary_components) == 1 and len(currencies) == 1:
            component = salary_components[0]
            fields["salary_min"] = component.get("minValue")
            fields["salary_max"] = component.get("maxValue")
            fields["salary_currency"] = component["currencyCode"]
            fields["salary_period"] = component.get("interval")

            salary_text = compensation.get(
                "scrapeableCompensationSalarySummary"
            )
            if salary_text:
                fields["salary"] = salary_text

        yield {
            key: value
            for key, value in fields.items()
            if value is not None
        }
