import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/taktile?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    response = ctx.fetch(SITE)
    if hasattr(response, "json"):
        data = response.json()
    elif hasattr(response, "text"):
        data = json.loads(response.text)
    else:
        data = json.loads(response)

    for item in data.get("jobs", []):
        if not item.get("isListed", True):
            continue

        location = item.get("location")
        locations = []
        if location:
            locations.append(location)
        for secondary in item.get("secondaryLocations") or []:
            secondary_location = secondary.get("location")
            if secondary_location:
                locations.append(secondary_location)

        kwargs = {
            "url": item["jobUrl"],
            "source": SITE,
            "title": item["title"],
            "company": "Taktile",
            "location": location,
            "description": item.get("descriptionPlain") or item.get("descriptionHtml"),
            "external_id": item.get("id"),
            "apply_url": item.get("applyUrl"),
            "employment_type": item.get("employmentType"),
            "department": item.get("department") or item.get("team"),
            "posted_at": item.get("publishedAt"),
        }
        if locations:
            kwargs["locations"] = locations

        workplace_type = item.get("workplaceType")
        if workplace_type:
            kwargs["workplace_type"] = workplace_type
        elif item.get("isRemote") is True:
            kwargs["workplace_type"] = "Remote"

        compensation = item.get("compensation") or {}
        salary_text = compensation.get("compensationTierSummary")
        if salary_text and item.get("shouldDisplayCompensationOnJobPostings"):
            kwargs["salary"] = salary_text

        salary_components = [
            component
            for component in compensation.get("summaryComponents") or []
            if component.get("compensationType") == "Salary"
        ]
        if len(salary_components) == 1:
            component = salary_components[0]
            if component.get("minValue") is not None:
                kwargs["salary_min"] = component["minValue"]
            if component.get("maxValue") is not None:
                kwargs["salary_max"] = component["maxValue"]
            if component.get("currencyCode"):
                kwargs["salary_currency"] = component["currencyCode"]
            if component.get("interval"):
                kwargs["salary_period"] = component["interval"]

        yield {key: value for key, value in kwargs.items() if value is not None}
