import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/fluidstack?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for item in payload.get("jobs", []):
        if item.get("isListed") is False:
            continue

        primary_location = item.get("location")
        description = item.get("descriptionPlain") or item.get("descriptionHtml")
        if not item.get("title") or not primary_location or not description or not item.get("jobUrl"):
            continue

        locations = []
        for location in [primary_location] + [
            entry.get("location")
            for entry in item.get("secondaryLocations", [])
            if isinstance(entry, dict)
        ]:
            if location and location not in locations:
                locations.append(location)

        record = {
            "url": item["jobUrl"],
            "source": SITE,
            "title": item["title"],
            "company": "Fluidstack",
            "location": primary_location,
            "locations": locations,
            "description": description,
            "external_id": item.get("id"),
            "apply_url": item.get("applyUrl"),
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "department": item.get("department"),
            "posted_at": item.get("publishedAt"),
        }

        compensation = item.get("compensation") or {}
        salary_component = next(
            (
                component
                for component in compensation.get("summaryComponents", [])
                if component.get("compensationType") == "Salary"
            ),
            None,
        )
        if salary_component:
            record.update(
                salary_min=salary_component.get("minValue"),
                salary_max=salary_component.get("maxValue"),
                salary_currency=salary_component.get("currencyCode"),
                salary_period=salary_component.get("interval"),
            )
            salary_text = compensation.get("scrapeableCompensationSalarySummary")
            if salary_text:
                record["salary"] = salary_text

        yield {key: value for key, value in record.items() if value is not None}
