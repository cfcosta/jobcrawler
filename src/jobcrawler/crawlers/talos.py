import json

Job = dict

SITE = "https://api.ashbyhq.com/posting-api/job-board/Talos-Trading?includeCompensation=true"
EXTRA_HOSTS = []


def _workplace_type(row):
    if row.get("workplaceType"):
        return row["workplaceType"]
    if row.get("isRemote") is True:
        return "Remote"
    return None


def _locations(row):
    values = []
    if row.get("location"):
        values.append(row["location"])
    for secondary in row.get("secondaryLocations") or []:
        if isinstance(secondary, dict):
            value = secondary.get("location")
        else:
            value = secondary
        if value:
            values.append(value)
    return values


def _salary_fields(row):
    compensation = row.get("compensation") or {}
    salary_components = [
        component
        for component in compensation.get("summaryComponents") or []
        if component.get("compensationType") == "Salary"
    ]
    if not salary_components:
        return {}

    currencies = {
        component.get("currencyCode")
        for component in salary_components
        if component.get("currencyCode")
    }
    periods = {
        component.get("interval")
        for component in salary_components
        if component.get("interval")
    }
    if len(currencies) > 1 or len(periods) > 1:
        return {}

    component = salary_components[0]
    fields = {}
    printed = compensation.get("compensationTierSummary")
    if printed:
        fields["salary"] = printed
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
    payload = json.loads(ctx.fetch(SITE))
    for row in payload.get("jobs", []):
        if row.get("isListed") is False:
            continue

        location = row.get("location")
        description = row.get("descriptionPlain")
        title = row.get("title")
        url = row.get("jobUrl")
        if not (title and location and description and url):
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Talos",
            "location": location,
            "locations": _locations(row),
            "description": description,
            "source": "ashby",
            "external_id": row.get("id"),
            "apply_url": row.get("applyUrl"),
            "employment_type": row.get("employmentType"),
            "workplace_type": _workplace_type(row),
            "department": row.get("department") or row.get("team"),
            "posted_at": row.get("publishedAt"),
        }
        fields.update(_salary_fields(row))
        yield Job(**{key: value for key, value in fields.items() if value is not None})


