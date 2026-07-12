
import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/camunda?includeCompensation=true"
EXTRA_HOSTS = []


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs", []):
        if not posting.get("isListed", True):
            continue

        location = posting.get("location")
        locations = [location] if location else []
        for secondary in posting.get("secondaryLocations") or []:
            secondary_location = secondary.get("location")
            if secondary_location and secondary_location not in locations:
                locations.append(secondary_location)

        workplace_type = posting.get("workplaceType")
        if not workplace_type and posting.get("isRemote") is True:
            workplace_type = "Remote"

        department = posting.get("department")
        team = posting.get("team")
        if department and team and team != department:
            department = department + " / " + team
        elif not department:
            department = team

        fields = {
            "url": posting.get("jobUrl"),
            "title": posting.get("title"),
            "company": "Camunda",
            "location": location,
            "description": posting.get("descriptionPlain"),
            "source": SITE,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": workplace_type,
            "department": department,
            "posted_at": posting.get("publishedAt"),
        }

        if locations:
            fields["locations"] = locations

        compensation = posting.get("compensation") or {}
        components = compensation.get("summaryComponents") or []
        if components:
            component = components[0]
            if component.get("minValue") is not None:
                fields["salary_min"] = component["minValue"]
            if component.get("maxValue") is not None:
                fields["salary_max"] = component["maxValue"]
            if component.get("currencyCode"):
                fields["salary_currency"] = component["currencyCode"]
            if component.get("interval"):
                fields["salary_period"] = component["interval"]

        printed_salary = compensation.get("scrapeableCompensationSalarySummary")
        if printed_salary:
            fields["salary"] = printed_salary

        yield {key: value for key, value in fields.items() if value is not None}
