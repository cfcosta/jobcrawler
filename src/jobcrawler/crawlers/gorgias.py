import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/gorgias?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs", []):
        if not posting.get("isListed", True):
            continue

        location = posting.get("location")
        description = posting.get("descriptionPlain")
        if not posting.get("title") or not location or not description:
            continue

        fields = {
            "url": posting.get("jobUrl"),
            "title": posting.get("title"),
            "company": "Gorgias",
            "location": location,
            "description": description,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": posting.get("employmentType"),
            "department": posting.get("department") or posting.get("team"),
            "posted_at": posting.get("publishedAt"),
        }

        secondary = [
            item.get("location")
            for item in posting.get("secondaryLocations") or []
            if item.get("location")
        ]
        if secondary:
            fields["locations"] = [location] + secondary

        workplace = posting.get("workplaceType")
        if workplace:
            fields["workplace_type"] = workplace
        elif posting.get("isRemote") is True:
            fields["workplace_type"] = "Remote"

        compensation = posting.get("compensation") or {}
        salary_components = [
            component
            for component in compensation.get("summaryComponents") or []
            if component.get("compensationType") == "Salary"
        ]
        if len(salary_components) == 1:
            salary_component = salary_components[0]
            required = ("minValue", "maxValue", "currencyCode", "interval")
            if all(salary_component.get(key) is not None for key in required):
                fields["salary_min"] = salary_component["minValue"]
                fields["salary_max"] = salary_component["maxValue"]
                fields["salary_currency"] = salary_component["currencyCode"]
                fields["salary_period"] = salary_component["interval"]
                printed = compensation.get("scrapeableCompensationSalarySummary")
                if printed:
                    fields["salary"] = printed

        fields["source"] = SITE
        yield {key: value for key, value in fields.items() if value is not None}
