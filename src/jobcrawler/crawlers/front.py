import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/frontcareers?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    response = ctx.fetch(SITE)
    data = json.loads(response)

    for posting in data.get("jobs", []):
        if not posting.get("isListed"):
            continue

        title = posting.get("title")
        location = posting.get("location")
        description = posting.get("descriptionPlain")
        url = posting.get("jobUrl")
        if not all((title, location, description, url)):
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Front",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": posting.get("employmentType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
        }

        secondary = posting.get("secondaryLocations") or []
        locations = [location]
        for item in secondary:
            secondary_location = (
                item.get("location") if isinstance(item, dict) else item
            )
            if secondary_location and secondary_location not in locations:
                locations.append(secondary_location)
        if len(locations) > 1:
            fields["locations"] = locations

        workplace_type = posting.get("workplaceType")
        if workplace_type:
            fields["workplace_type"] = workplace_type
        elif posting.get("isRemote") is True:
            fields["workplace_type"] = "Remote"

        compensation = posting.get("compensation") or {}
        salary_components = [
            component
            for component in compensation.get("summaryComponents") or []
            if component.get("compensationType") == "Salary"
            and component.get("minValue") is not None
            and component.get("maxValue") is not None
            and component.get("currencyCode")
            and component.get("interval")
        ]
        if len(salary_components) == 1:
            component = salary_components[0]
            fields["salary_min"] = component["minValue"]
            fields["salary_max"] = component["maxValue"]
            fields["salary_currency"] = component["currencyCode"]
            fields["salary_period"] = component["interval"]
            salary_text = compensation.get("compensationTierSummary")
            if salary_text:
                fields["salary"] = salary_text

        yield {key: value for key, value in fields.items() if value is not None}
