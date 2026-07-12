import json


SITE = "https://api.ashbyhq.com/posting-api/job-board/livekit?includeCompensation=true"
EXTRA_HOSTS = []


def _present(value):
    return value is not None and value != ""


def _salary_fields(posting):
    compensation = posting.get("compensation") or {}
    components = [
        component
        for component in compensation.get("summaryComponents") or []
        if component.get("compensationType") == "Salary"
        and _present(component.get("currencyCode"))
        and (
            _present(component.get("minValue"))
            or _present(component.get("maxValue"))
        )
    ]

    fields = {}
    printed = compensation.get("scrapeableCompensationSalarySummary")
    if _present(printed):
        fields["salary"] = printed

    signatures = {
        (
            component.get("currencyCode"),
            component.get("interval"),
            component.get("minValue"),
            component.get("maxValue"),
        )
        for component in components
    }
    if len(signatures) != 1:
        return fields

    currency, period, minimum, maximum = next(iter(signatures))
    if _present(minimum):
        fields["salary_min"] = minimum
    if _present(maximum):
        fields["salary_max"] = maximum
    if _present(currency):
        fields["salary_currency"] = currency
    if _present(period):
        fields["salary_period"] = period
    return fields


def crawl(ctx):
    response = ctx.fetch(SITE)
    payload = json.loads(response)

    for posting in payload.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        title = posting.get("title")
        description = posting.get("descriptionPlain")
        location = posting.get("location")
        url = posting.get("jobUrl")
        if not all((_present(title), _present(description), _present(location), _present(url))):
            continue

        fields = {
            "source": SITE,
            "url": url,
            "title": title,
            "company": "LiveKit",
            "location": location,
            "description": description,
        }

        locations = [location]
        for secondary in posting.get("secondaryLocations") or []:
            value = (
                secondary.get("location")
                if isinstance(secondary, dict)
                else secondary
            )
            if _present(value) and value not in locations:
                locations.append(value)
        fields["locations"] = locations

        optional = {
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        fields.update({
            key: value for key, value in optional.items() if _present(value)
        })
        fields.update(_salary_fields(posting))

        yield fields
