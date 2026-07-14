
import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/jellyfish?includeCompensation=true"
EXTRA_HOSTS = []


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for item in data.get("jobs", []):
        if not item.get("isListed", True):
            continue

        locations = []
        if item.get("location"):
            locations.append(item["location"])
        for secondary in item.get("secondaryLocations") or []:
            value = secondary.get("location") if isinstance(secondary, dict) else secondary
            if value and value not in locations:
                locations.append(value)

        fields = {
            "url": item["jobUrl"],
            "title": item["title"].strip(),
            "company": "Jellyfish",
            "location": item["location"],
            "locations": locations,
            "description": item["descriptionPlain"],
            "source": SITE,
            "external_id": item.get("id"),
            "apply_url": item.get("applyUrl"),
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "department": item.get("department"),
            "posted_at": item.get("publishedAt"),
        }

        compensation = item.get("compensation") or {}
        salary_components = [
            component
            for component in compensation.get("summaryComponents") or []
            if component.get("compensationType") == "Salary"
            and component.get("currencyCode")
            and (component.get("minValue") is not None or component.get("maxValue") is not None)
        ]
        if len(salary_components) == 1:
            component = salary_components[0]
            fields.update(
                salary_min=component.get("minValue"),
                salary_max=component.get("maxValue"),
                salary_currency=component.get("currencyCode"),
                salary_period=component.get("interval"),
            )
            printed = (
                compensation.get("scrapeableCompensationSalarySummary")
                or compensation.get("compensationTierSummary")
            )
            if printed:
                fields["salary"] = printed

        yield {key: value for key, value in fields.items() if value is not None}
