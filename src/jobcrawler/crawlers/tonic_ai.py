import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/TonicAI?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    response = ctx.fetch(SITE)
    payload = json.loads(response)

    for posting in payload.get("jobs", []):
        if posting.get("isListed") is False:
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
            "source": SITE,
            "title": posting.get("title"),
            "company": "Tonic.ai",
            "location": location,
            "description": posting.get("descriptionPlain"),
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
        salary_text = compensation.get("compensationTierSummary")
        if salary_text:
            fields["salary"] = salary_text

        for component in compensation.get("summaryComponents") or []:
            minimum = component.get("minValue")
            maximum = component.get("maxValue")
            currency = component.get("currencyCode")
            if (
                component.get("compensationType") == "Salary"
                and (minimum is not None or maximum is not None)
                and currency
            ):
                if minimum is not None:
                    fields["salary_min"] = minimum
                if maximum is not None:
                    fields["salary_max"] = maximum
                fields["salary_currency"] = currency
                if component.get("interval"):
                    fields["salary_period"] = component["interval"]
                break

        yield {key: value for key, value in fields.items() if value is not None}
