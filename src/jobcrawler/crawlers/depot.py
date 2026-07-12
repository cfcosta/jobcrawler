import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/depot?includeCompensation=true'
EXTRA_HOSTS = []


def _response_json(response):
    body = response.read() if hasattr(response, "read") else response
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    return json.loads(body)


def _compensation_fields(job):
    compensation = job.get("compensation") or {}
    components = [
        component
        for component in (compensation.get("summaryComponents") or [])
        if component.get("compensationType") == "Salary"
        and component.get("minValue") is not None
        and component.get("maxValue") is not None
        and component.get("currencyCode")
        and component.get("interval")
    ]
    if not components:
        return {}

    currencies = {component["currencyCode"] for component in components}
    intervals = {component["interval"] for component in components}
    if len(currencies) != 1 or len(intervals) != 1:
        return {}

    result = {
        "salary_min": min(component["minValue"] for component in components),
        "salary_max": max(component["maxValue"] for component in components),
        "salary_currency": components[0]["currencyCode"],
        "salary_period": components[0]["interval"],
    }
    printed = (
        compensation.get("compensationTierSummary")
        or compensation.get("scrapeableCompensationSalarySummary")
    )
    if printed:
        result["salary"] = printed
    return result


def crawl(ctx):
    data = _response_json(ctx.fetch(SITE))
    seen = set()

    for posting in data.get("jobs", []):
        if posting.get("isListed") is not True:
            continue

        external_id = posting.get("id")
        url = posting.get("jobUrl")
        key = external_id or url
        if not key or key in seen:
            continue
        seen.add(key)

        title = posting.get("title")
        location = posting.get("location")
        description = posting.get("descriptionPlain")
        if not title or not location or not description or not url:
            continue

        values = {
            "url": url,
            "title": title.strip(),
            "company": "Depot",
            "location": location,
            "description": description,
            "source": SITE,
        }

        locations = [location]
        locations.extend(
            item["location"]
            for item in (posting.get("secondaryLocations") or [])
            if item.get("location")
        )
        values["locations"] = locations

        optional = {
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department") or posting.get("team"),
            "posted_at": posting.get("publishedAt"),
            "external_id": external_id,
            "apply_url": posting.get("applyUrl"),
        }
        values.update({key: value for key, value in optional.items() if value is not None})
        values.update(_compensation_fields(posting))

        yield values
