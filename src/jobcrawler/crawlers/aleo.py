import json


SITE = 'https://api.ashbyhq.com/posting-api/job-board/provable?includeCompensation=true'
EXTRA_HOSTS = []


def _compensation_fields(posting):
    compensation = posting.get("compensation") or {}
    fields = {}

    printed = (
        compensation.get("scrapeableCompensationSalarySummary")
        or compensation.get("compensationTierSummary")
    )
    if printed:
        fields["salary"] = printed

    components = [
        component
        for component in compensation.get("summaryComponents", [])
        if component.get("minValue") is not None
        or component.get("maxValue") is not None
    ]
    currencies = {
        component.get("currencyCode")
        for component in components
        if component.get("currencyCode")
    }

    if len(components) == 1 and len(currencies) <= 1:
        component = components[0]
        if component.get("minValue") is not None:
            fields["salary_min"] = component["minValue"]
        if component.get("maxValue") is not None:
            fields["salary_max"] = component["maxValue"]
        if component.get("currencyCode"):
            fields["salary_currency"] = component["currencyCode"]
        if component.get("interval"):
            fields["salary_period"] = component["interval"]

    return fields


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    seen = set()

    for posting in data.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        external_id = posting.get("id")
        url = posting.get("jobUrl")
        dedupe_key = external_id or url
        if not dedupe_key or dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        title = posting.get("title")
        location = posting.get("location")
        description = posting.get("descriptionPlain")
        if not title or not location or not description or not url:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Provable Inc.",
            "location": location,
            "description": description,
            "source": SITE,
        }

        secondary_locations = [
            item.get("location")
            for item in posting.get("secondaryLocations", [])
            if isinstance(item, dict) and item.get("location")
        ]
        if secondary_locations:
            fields["locations"] = [location] + secondary_locations

        optional = {
            "employment_type": posting.get("employmentType"),
            "department": posting.get("department") or posting.get("team"),
            "posted_at": posting.get("publishedAt"),
            "external_id": external_id,
            "apply_url": posting.get("applyUrl"),
        }
        fields.update({
            key: value
            for key, value in optional.items()
            if value is not None and value != ""
        })

        workplace_type = posting.get("workplaceType")
        if workplace_type:
            fields["workplace_type"] = workplace_type
        elif posting.get("isRemote") is True:
            fields["workplace_type"] = "Remote"

        fields.update(_compensation_fields(posting))
        yield fields
