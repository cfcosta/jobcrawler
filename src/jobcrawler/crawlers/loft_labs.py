import json


SITE = "https://api.ashbyhq.com/posting-api/job-board/vClusterLabs?includeCompensation=true"
EXTRA_HOSTS = []


def _salary_fields(compensation):
    if not isinstance(compensation, dict):
        return {}

    salary_components = [
        component
        for component in compensation.get("summaryComponents", [])
        if component.get("compensationType") == "Salary"
        and (
            component.get("minValue") is not None
            or component.get("maxValue") is not None
        )
    ]
    if len(salary_components) != 1:
        return {}

    component = salary_components[0]
    fields = {}

    if component.get("minValue") is not None:
        fields["salary_min"] = component["minValue"]
    if component.get("maxValue") is not None:
        fields["salary_max"] = component["maxValue"]
    if component.get("currencyCode"):
        fields["salary_currency"] = component["currencyCode"]
    if component.get("interval"):
        fields["salary_period"] = component["interval"]

    printed = compensation.get("compensationTierSummary")
    if printed:
        fields["salary"] = printed

    return fields


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        title = (posting.get("title") or "").strip()
        location = (posting.get("location") or "").strip()
        description = (posting.get("descriptionPlain") or "").strip()
        url = posting.get("jobUrl")

        if not title or not location or not description or not url:
            continue

        locations = [posting["location"]]
        for secondary in posting.get("secondaryLocations") or []:
            secondary_location = secondary.get("location")
            if secondary_location:
                locations.append(secondary_location)

        fields = {
            "url": url,
            "title": title,
            "company": "vCluster Labs",
            "location": location,
            "locations": locations,
            "description": description,
            "source": SITE,
        }

        optional = {
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department") or posting.get("team"),
            "posted_at": posting.get("publishedAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        fields.update({
            key: value
            for key, value in optional.items()
            if value is not None and value != ""
        })
        fields.update(_salary_fields(posting.get("compensation")))

        yield fields
