import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/hackerone?includeCompensation=true"
EXTRA_HOSTS = []


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs", []):
        if not posting.get("isListed", True):
            continue

        description = posting.get("descriptionPlain")
        if not description:
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
            "company": "HackerOne",
            "location": location,
            "source": SITE,
            "description": description,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department") or posting.get("team"),
            "posted_at": posting.get("publishedAt"),
        }
        if locations:
            fields["locations"] = locations

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
        currencies = {component["currencyCode"] for component in salary_components}
        intervals = {component["interval"] for component in salary_components}
        if salary_components and len(currencies) == 1 and len(intervals) == 1:
            fields["salary_min"] = min(
                component["minValue"] for component in salary_components
            )
            fields["salary_max"] = max(
                component["maxValue"] for component in salary_components
            )
            fields["salary_currency"] = next(iter(currencies))
            fields["salary_period"] = next(iter(intervals))

            salary_text = compensation.get("scrapeableCompensationSalarySummary")
            if salary_text:
                fields["salary"] = salary_text

        yield {
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        }
