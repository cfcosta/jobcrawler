import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/Union?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    response = ctx.fetch(SITE)
    if isinstance(response, str):
        data = json.loads(response)
    elif isinstance(response, (bytes, bytearray)):
        data = json.loads(response.decode("utf-8"))
    elif hasattr(response, "json"):
        value = response.json()
        data = value
    else:
        data = response

    for item in data.get("jobs", []):
        if not item.get("isListed", True):
            continue

        description = item.get("descriptionPlain")
        if not description:
            continue

        location = item.get("location")
        locations = []
        if location:
            locations.append(location)
        for secondary in item.get("secondaryLocations") or []:
            value = secondary.get("location") if isinstance(secondary, dict) else secondary
            if value and value not in locations:
                locations.append(value)

        kwargs = {
            "url": item.get("jobUrl"),
            "title": item.get("title"),
            "company": "Union.ai",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": item.get("id"),
            "apply_url": item.get("applyUrl"),
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "department": item.get("department") or item.get("team"),
            "posted_at": item.get("publishedAt"),
        }
        if locations:
            kwargs["locations"] = locations

        compensation = item.get("compensation") or {}
        components = compensation.get("summaryComponents") or []
        usable = [
            component for component in components
            if component.get("minValue") is not None
            or component.get("maxValue") is not None
        ]
        if usable:
            currencies = {component.get("currencyCode") for component in usable if component.get("currencyCode")}
            periods = {component.get("interval") for component in usable if component.get("interval")}
            if len(currencies) <= 1 and len(periods) <= 1:
                mins = [component["minValue"] for component in usable if component.get("minValue") is not None]
                maxes = [component["maxValue"] for component in usable if component.get("maxValue") is not None]
                if mins:
                    kwargs["salary_min"] = min(mins)
                if maxes:
                    kwargs["salary_max"] = max(maxes)
                if currencies:
                    kwargs["salary_currency"] = next(iter(currencies))
                if periods:
                    kwargs["salary_period"] = next(iter(periods))
                printed = compensation.get("compensationTierSummary") or compensation.get("scrapeableCompensationSalarySummary")
                if printed:
                    kwargs["salary"] = printed

        yield {key: value for key, value in kwargs.items() if value is not None and value != ""}
