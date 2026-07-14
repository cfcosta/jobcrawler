import json


SITE = "https://api.ashbyhq.com/posting-api/job-board/moss?includeCompensation=true"
EXTRA_HOSTS = []
COMPANY = "Moss"


def _present(value):
    return value is not None and value != ""


def _salary_fields(compensation):
    if not isinstance(compensation, dict):
        return {}

    fields = {}
    printed = (
        compensation.get("compensationTierSummary")
        or compensation.get("scrapeableCompensationSalarySummary")
    )
    if _present(printed):
        fields["salary"] = printed

    for component in compensation.get("summaryComponents") or []:
        if not isinstance(component, dict):
            continue

        minimum = component.get("minValue")
        maximum = component.get("maxValue")
        currency = component.get("currencyCode")
        period = component.get("interval")
        if not any(_present(value) for value in (minimum, maximum, currency, period)):
            continue

        if _present(minimum):
            fields["salary_min"] = minimum
        if _present(maximum):
            fields["salary_max"] = maximum
        if _present(currency):
            fields["salary_currency"] = currency
        if _present(period):
            fields["salary_period"] = period
        break

    return fields


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for posting in payload.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        description = posting.get("descriptionPlain")
        if not _present(description):
            continue

        location = posting.get("location")
        locations = []
        if _present(location):
            locations.append(location)

        for secondary in posting.get("secondaryLocations") or []:
            secondary_location = (
                secondary.get("location")
                if isinstance(secondary, dict)
                else secondary
            )
            if _present(secondary_location):
                locations.append(secondary_location)

        fields = {
            "url": posting.get("jobUrl"),
            "title": posting.get("title"),
            "company": COMPANY,
            "location": location,
            "description": description,
            "source": SITE,
        }

        optional = {
            "locations": locations or None,
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
            if _present(value)
        })
        fields.update(_salary_fields(posting.get("compensation")))

        yield fields
