import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/Nango?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    response = ctx.fetch(SITE)
    data = json.loads(response)

    for posting in data.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        primary_location = posting.get("location")
        locations = []
        if primary_location:
            locations.append(primary_location)
        for secondary in posting.get("secondaryLocations") or []:
            value = secondary.get("location")
            if value:
                locations.append(value)

        fields = {
            "url": posting.get("jobUrl"),
            "title": posting.get("title"),
            "company": "Nango",
            "location": primary_location,
            "locations": locations or None,
            "description": posting.get("descriptionPlain"),
            "source": SITE,
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }

        compensation = posting.get("compensation") or {}
        for component in compensation.get("summaryComponents") or []:
            if component.get("compensationType") == "Salary":
                fields["salary_min"] = component.get("minValue")
                fields["salary_max"] = component.get("maxValue")
                fields["salary_currency"] = component.get("currencyCode")
                fields["salary_period"] = component.get("interval")
                salary_text = compensation.get("scrapeableCompensationSalarySummary")
                if salary_text:
                    fields["salary"] = salary_text
                break

        yield {key: value for key, value in fields.items() if value is not None}
