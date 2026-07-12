import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/midjourney?includeCompensation=true'
EXTRA_HOSTS = []


def _json_payload(response):
    if isinstance(response, (dict, list)):
        return response
    if isinstance(response, (bytes, bytearray)):
        response = response.decode("utf-8")
    if isinstance(response, str):
        return json.loads(response)
    if hasattr(response, "json"):
        return response.json()
    if hasattr(response, "text"):
        return json.loads(response.text)
    raise TypeError("Unsupported fetch response")


def _compensation_fields(job):
    compensation = job.get("compensation") or {}
    components = compensation.get("summaryComponents") or []
    salary_components = [
        component for component in components
        if component.get("minValue") is not None
        or component.get("maxValue") is not None
    ]
    if not salary_components:
        return {}

    currencies = {
        component.get("currencyCode")
        for component in salary_components
        if component.get("currencyCode")
    }
    if len(currencies) > 1:
        return {}

    component = salary_components[0]
    result = {}
    if component.get("minValue") is not None:
        result["salary_min"] = component["minValue"]
    if component.get("maxValue") is not None:
        result["salary_max"] = component["maxValue"]
    if component.get("currencyCode"):
        result["salary_currency"] = component["currencyCode"]
    if component.get("interval"):
        result["salary_period"] = component["interval"]

    printed = (
        compensation.get("scrapeableCompensationSalarySummary")
        or compensation.get("compensationTierSummary")
    )
    if printed:
        result["salary"] = printed
    return result


def crawl(ctx):
    payload = _json_payload(ctx.fetch(SITE))
    for item in payload.get("jobs", []):
        if item.get("isListed") is False:
            continue

        description = item.get("descriptionPlain")
        if not description:
            continue

        kwargs = {
            "url": item["jobUrl"],
            "title": item["title"],
            "company": "Midjourney",
            "location": item["location"],
            "description": description,
            "source": SITE,
            "external_id": item.get("id"),
            "apply_url": item.get("applyUrl"),
            "department": item.get("department") or item.get("team"),
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "posted_at": item.get("publishedAt"),
        }

        locations = []
        if item.get("location"):
            locations.append(item["location"])
        for secondary in item.get("secondaryLocations") or []:
            value = secondary.get("location")
            if value and value not in locations:
                locations.append(value)
        if locations:
            kwargs["locations"] = locations

        kwargs.update(_compensation_fields(item))
        yield {key: value for key, value in kwargs.items() if value is not None} 
