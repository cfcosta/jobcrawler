import json


SITE = "https://api.ashbyhq.com/posting-api/job-board/arthur-ai?includeCompensation=true"
EXTRA_HOSTS = []


def _present(value):
    return value if value not in (None, "") else None


def _salary_fields(item):
    compensation = item.get("compensation") or {}
    components = [
        component
        for component in (compensation.get("summaryComponents") or [])
        if isinstance(component, dict)
        and (component.get("minValue") is not None or component.get("maxValue") is not None)
    ]
    currencies = {
        component.get("currencyCode")
        for component in components
        if component.get("currencyCode")
    }
    result = {}

    # Avoid combining ranges denominated in different currencies.
    if components and len(currencies) <= 1:
        component = components[0]
        if component.get("minValue") is not None:
            result["salary_min"] = component["minValue"]
        if component.get("maxValue") is not None:
            result["salary_max"] = component["maxValue"]
        if component.get("currencyCode"):
            result["salary_currency"] = component["currencyCode"]
        interval = component.get("interval")
        if interval:
            result["salary_period"] = interval

    printed = compensation.get("compensationTierSummary")
    if not isinstance(printed, str):
        printed = compensation.get("scrapeableCompensationSalarySummary")
        if not isinstance(printed, str):
            printed = None
    if printed:
        result["salary"] = printed
    return result


def crawl(ctx):
    response = ctx.fetch(SITE)
    payload = json.loads(response)

    for item in payload.get("jobs", []):
        if item.get("isListed") is False:
            continue

        location = _present(item.get("location"))
        secondary = [
            entry.get("location")
            for entry in (item.get("secondaryLocations") or [])
            if isinstance(entry, dict) and entry.get("location")
        ]

        fields = {
            "url": _present(item.get("jobUrl")),
            "title": _present(item.get("title")),
            "company": "Arthur AI",
            "location": location,
            "description": _present(
                item.get("descriptionPlain") or item.get("descriptionHtml")
            ),
            "source": SITE,
            "employment_type": _present(item.get("employmentType")),
            "workplace_type": _present(item.get("workplaceType")),
            "department": _present(item.get("department") or item.get("team")),
            "posted_at": _present(item.get("publishedAt")),
            "external_id": _present(item.get("id")),
            "apply_url": _present(item.get("applyUrl")),
        }
        if secondary:
            fields["locations"] = ([location] if location else []) + secondary
        fields.update(_salary_fields(item))

        yield {key: value for key, value in fields.items() if value is not None}
