import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/oscilar?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs", []):
        if not posting.get("isListed", True):
            continue

        location = posting.get("location")
        locations = []
        if location:
            locations.append(location)
        for secondary in posting.get("secondaryLocations") or []:
            secondary_location = secondary.get("location")
            if secondary_location:
                locations.append(secondary_location)

        fields = {
            "url": posting.get("jobUrl"),
            "title": posting.get("title"),
            "company": "Oscilar",
            "location": location,
            "description": posting.get("descriptionPlain"),
            "source": SITE,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
        }

        if locations:
            fields["locations"] = locations

        compensation = posting.get("compensation") or {}
        components = compensation.get("summaryComponents") or []
        salary_components = [
            component
            for component in components
            if component.get("minValue") is not None
            or component.get("maxValue") is not None
        ]
        currencies = {
            component.get("currencyCode")
            for component in salary_components
            if component.get("currencyCode")
        }
        if salary_components and len(currencies) <= 1:
            component = salary_components[0]
            fields["salary_min"] = component.get("minValue")
            fields["salary_max"] = component.get("maxValue")
            fields["salary_currency"] = component.get("currencyCode")
            fields["salary_period"] = component.get("interval")
            summary = compensation.get("compensationTierSummary")
            if summary:
                fields["salary"] = summary

        yield {key: value for key, value in fields.items() if value is not None}
