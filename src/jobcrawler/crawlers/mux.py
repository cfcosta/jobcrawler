import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/mux?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    raw = ctx.fetch(SITE)
    data = json.loads(raw)

    for posting in data.get("jobs", []):
        if not posting.get("isListed"):
            continue

        location = posting.get("location")
        locations = []
        if location:
            locations.append(location)
        for secondary in posting.get("secondaryLocations") or []:
            value = secondary.get("location") if isinstance(secondary, dict) else secondary
            if value:
                locations.append(value)

        fields = {
            "url": posting.get("jobUrl"),
            "source": SITE,
            "title": posting.get("title"),
            "company": "Mux",
            "location": location,
            "description": posting.get("descriptionPlain"),
            "external_id": posting.get("id"),
            "employment_type": posting.get("employmentType"),
            "department": posting.get("department") or posting.get("team"),
            "posted_at": posting.get("publishedAt"),
            "apply_url": posting.get("applyUrl"),
        }

        if locations:
            fields["locations"] = locations

        workplace = posting.get("workplaceType")
        if workplace:
            fields["workplace_type"] = workplace
        elif posting.get("isRemote") is True:
            fields["workplace_type"] = "Remote"

        compensation = posting.get("compensation") or {}
        printed_salary = compensation.get("scrapeableCompensationSalarySummary")
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
