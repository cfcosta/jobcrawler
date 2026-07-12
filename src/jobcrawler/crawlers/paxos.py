import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/paxos?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    response = ctx.fetch(SITE)
    payload = json.loads(response)

    for posting in payload.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        description = posting.get("descriptionPlain")
        title = posting.get("title")
        location = posting.get("location")
        url = posting.get("jobUrl")
        if not title or not location or not description or not url:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Paxos",
            "location": location,
            "description": description,
            "source": SITE,
        }

        locations = [location]
        for secondary in posting.get("secondaryLocations") or []:
            secondary_location = secondary.get("location")
            if secondary_location and secondary_location not in locations:
                locations.append(secondary_location)
        fields["locations"] = locations

        optional_fields = {
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department") or posting.get("team"),
            "posted_at": posting.get("publishedAt"),
        }
        fields.update({
            key: value for key, value in optional_fields.items()
            if value is not None and value != ""
        })

        compensation = posting.get("compensation") or {}
        salary_text = compensation.get("compensationTierSummary")
        if salary_text:
            fields["salary"] = salary_text

        salary_components = [
            component
            for component in compensation.get("summaryComponents") or []
            if component.get("compensationType") == "Salary"
        ]
        if len(salary_components) == 1:
            component = salary_components[0]
            salary_fields = {
                "salary_min": component.get("minValue"),
                "salary_max": component.get("maxValue"),
                "salary_currency": component.get("currencyCode"),
                "salary_period": component.get("interval"),
            }
            fields.update({
                key: value for key, value in salary_fields.items()
                if value is not None and value != ""
            })

        yield fields
