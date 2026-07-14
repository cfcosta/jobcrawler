import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/footprint?includeCompensation=true'
EXTRA_HOSTS = []


def _salary_fields(job):
    compensation = job.get("compensation") or {}
    for component in compensation.get("summaryComponents") or []:
        if component.get("compensationType") != "Salary":
            continue

        fields = {}
        if component.get("minValue") is not None:
            fields["salary_min"] = component["minValue"]
        if component.get("maxValue") is not None:
            fields["salary_max"] = component["maxValue"]
        if component.get("currencyCode"):
            fields["salary_currency"] = component["currencyCode"]
        if component.get("interval"):
            fields["salary_period"] = component["interval"]

        printed = compensation.get("scrapeableCompensationSalarySummary")
        if printed:
            fields["salary"] = printed
        return fields

    return {}


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs") or []:
        if posting.get("isListed") is False:
            continue

        description = posting.get("descriptionPlain")
        if not description:
            continue

        fields = {
            "url": posting["jobUrl"],
            "title": posting["title"],
            "company": "Footprint",
            "location": posting["location"],
            "description": description,
            "source": SITE,
        }

        secondary = [
            item.get("location")
            for item in posting.get("secondaryLocations") or []
            if item.get("location")
        ]
        if secondary:
            fields["locations"] = [posting["location"]] + secondary

        optional = {
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        fields.update({key: value for key, value in optional.items() if value})
        fields.update(_salary_fields(posting))

        yield fields
