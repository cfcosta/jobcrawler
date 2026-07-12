
import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/ellipsislabs?includeCompensation=true"
EXTRA_HOSTS = []


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs", []):
        if not posting.get("isListed", True):
            continue

        location = posting.get("location")
        fields = {
            "url": posting["jobUrl"],
            "title": posting["title"],
            "company": "Ellipsis Labs",
            "location": location,
            "description": posting.get("descriptionPlain"),
            "source": SITE,
            "external_id": posting.get("id"),
            "department": posting.get("department") or posting.get("team"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "posted_at": posting.get("publishedAt"),
            "apply_url": posting.get("applyUrl"),
        }

        locations = []
        if location:
            locations.append(location)
        for secondary in posting.get("secondaryLocations") or []:
            if isinstance(secondary, dict):
                value = secondary.get("location")
            else:
                value = secondary
            if value:
                locations.append(value)
        if locations:
            fields["locations"] = locations

        compensation = posting.get("compensation") or {}
        printed_salary = compensation.get("compensationTierSummary")
        if printed_salary:
            fields["salary"] = printed_salary

        salary_components = [
            component
            for component in compensation.get("summaryComponents") or []
            if component.get("compensationType") == "Salary"
        ]
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

        yield {key: value for key, value in fields.items() if value is not None}
