import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/cryptio?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        location = posting.get("location")
        locations = []
        if location:
            locations.append(location)
        for secondary in posting.get("secondaryLocations") or []:
            value = secondary.get("location")
            if value:
                locations.append(value)

        fields = {
            "url": posting.get("jobUrl"),
            "source": SITE,
            "title": posting.get("title"),
            "company": "Cryptio",
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
        if posting.get("shouldDisplayCompensationOnJobPostings"):
            printed_salary = compensation.get("compensationTierSummary")
            if printed_salary:
                fields["salary"] = printed_salary

            salary_components = [
                component
                for component in compensation.get("summaryComponents") or []
                if component.get("compensationType") == "Salary"
            ]
            if salary_components:
                component = salary_components[0]
                if component.get("minValue") is not None:
                    fields["salary_min"] = component["minValue"]
                if component.get("maxValue") is not None:
                    fields["salary_max"] = component["maxValue"]
                if component.get("currencyCode"):
                    fields["salary_currency"] = component["currencyCode"]
                if component.get("interval"):
                    fields["salary_period"] = component["interval"]

        yield {key: value for key, value in fields.items() if value is not None and value != ""}
