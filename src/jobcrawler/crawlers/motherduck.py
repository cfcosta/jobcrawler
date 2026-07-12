import json


SITE = "https://api.ashbyhq.com/posting-api/job-board/motherduck?includeCompensation=true"
EXTRA_HOSTS = []


def _salary_fields(job):
    compensation = job.get("compensation") or {}
    salary_components = [
        component
        for component in compensation.get("summaryComponents") or []
        if component.get("compensationType") == "Salary"
    ]
    if len(salary_components) != 1:
        return {}

    component = salary_components[0]
    result = {}

    salary_text = compensation.get("scrapeableCompensationSalarySummary")
    if salary_text:
        result["salary"] = salary_text
    if component.get("minValue") is not None:
        result["salary_min"] = component["minValue"]
    if component.get("maxValue") is not None:
        result["salary_max"] = component["maxValue"]
    if component.get("currencyCode"):
        result["salary_currency"] = component["currencyCode"]
    if component.get("interval"):
        result["salary_period"] = component["interval"]

    return result


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs", []):
        if posting.get("isListed") is not True:
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
            "company": "MotherDuck",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "posted_at": posting.get("publishedAt"),
            "employment_type": posting.get("employmentType"),
            "department": posting.get("department") or posting.get("team"),
        }

        if locations:
            fields["locations"] = locations

        workplace_type = posting.get("workplaceType")
        if workplace_type:
            fields["workplace_type"] = workplace_type
        elif posting.get("isRemote") is True:
            fields["workplace_type"] = "Remote"

        fields.update(_salary_fields(posting))

        yield {
            key: value
            for key, value in fields.items()
            if value is not None
        }
