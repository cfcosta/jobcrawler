import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/exa?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    response = ctx.fetch(SITE)
    payload = json.loads(response)

    for posting in payload.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        url = posting.get("jobUrl") or posting.get("applyUrl")
        title = posting.get("title")
        location = posting.get("location")
        description = posting.get("descriptionPlain")

        if not url or not title or not location or not description:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Exa",
            "location": location,
            "description": description,
            "source": SITE,
        }

        secondary = posting.get("secondaryLocations") or []
        locations = [location]
        locations.extend(
            item["location"]
            for item in secondary
            if isinstance(item, dict) and item.get("location")
        )
        if len(locations) > 1:
            fields["locations"] = locations

        optional = {
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department") or posting.get("team"),
            "posted_at": posting.get("publishedAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        fields.update({key: value for key, value in optional.items() if value is not None})

        compensation = posting.get("compensation") or {}
        salary_components = [
            component
            for component in compensation.get("summaryComponents", [])
            if component.get("compensationType") == "Salary"
            and component.get("currencyCode")
            and (
                component.get("minValue") is not None
                or component.get("maxValue") is not None
            )
        ]
        signatures = {
            (component.get("currencyCode"), component.get("interval"))
            for component in salary_components
        }
        if salary_components and len(signatures) == 1:
            component = salary_components[0]
            salary_text = compensation.get("scrapeableCompensationSalarySummary")
            if salary_text:
                fields["salary"] = salary_text
            if component.get("minValue") is not None:
                fields["salary_min"] = component["minValue"]
            if component.get("maxValue") is not None:
                fields["salary_max"] = component["maxValue"]
            fields["salary_currency"] = component["currencyCode"]
            if component.get("interval"):
                fields["salary_period"] = component["interval"]

        yield fields
