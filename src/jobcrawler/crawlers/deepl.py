import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/DeepL?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs", []):
        if not posting.get("isListed", True):
            continue

        fields = {
            "url": posting["jobUrl"],
            "title": posting["title"],
            "company": "DeepL",
            "location": posting["location"],
            "description": posting["descriptionPlain"],
            "source": SITE,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department") or posting.get("team"),
            "posted_at": posting.get("publishedAt"),
        }

        locations = [posting["location"]]
        for secondary in posting.get("secondaryLocations") or []:
            location = secondary.get("location")
            if location and location not in locations:
                locations.append(location)
        if len(locations) > 1:
            fields["locations"] = locations

        compensation = posting.get("compensation") or {}
        salary_text = compensation.get("scrapeableCompensationSalarySummary")
        salary_components = [
            component
            for component in compensation.get("summaryComponents") or []
            if component.get("compensationType") == "Salary"
        ]

        if salary_text:
            fields["salary"] = salary_text

        if len(salary_components) == 1:
            component = salary_components[0]
            if component.get("minValue") is not None:
                fields["salary_min"] = component["minValue"]
            if component.get("maxValue") is not None:
                fields["salary_max"] = component["maxValue"]
            if component.get("currencyCode"):
                fields["salary_currency"] = component["currencyCode"]
            if component.get("interval"):
                fields["salary_period"] = component["interval"]

        yield {
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        }
