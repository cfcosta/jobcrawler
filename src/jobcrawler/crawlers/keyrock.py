import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/keyrock?includeCompensation=true"
EXTRA_HOSTS = []


def _put(fields, name, value):
    if value is not None and value != "":
        fields[name] = value


def _add_compensation(fields, compensation):
    if not compensation:
        return

    printed = (
        compensation.get("compensationTierSummary")
        or compensation.get("scrapeableCompensationSalarySummary")
    )
    _put(fields, "salary", printed)

    components = compensation.get("summaryComponents") or []
    salary_components = [
        component
        for component in components
        if component.get("minValue") is not None
        or component.get("maxValue") is not None
    ]
    if not salary_components:
        return

    currencies = {
        component.get("currencyCode")
        for component in salary_components
        if component.get("currencyCode")
    }
    if len(currencies) > 1:
        return

    component = salary_components[0]
    _put(fields, "salary_min", component.get("minValue"))
    _put(fields, "salary_max", component.get("maxValue"))
    if currencies:
        fields["salary_currency"] = next(iter(currencies))
    _put(fields, "salary_period", component.get("interval"))


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for posting in payload.get("jobs", []):
        if not posting.get("isListed", False):
            continue

        location = posting.get("location")
        description = posting.get("descriptionPlain")
        url = posting.get("jobUrl")
        title = posting.get("title")
        if not all((location, description, url, title)):
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Keyrock",
            "location": location,
            "description": description,
            "source": SITE,
        }

        locations = [location]
        locations.extend(
            item["location"]
            for item in posting.get("secondaryLocations", [])
            if item.get("location")
        )
        fields["locations"] = locations

        _put(fields, "employment_type", posting.get("employmentType"))
        _put(fields, "workplace_type", posting.get("workplaceType"))
        _put(fields, "department", posting.get("department") or posting.get("team"))
        _put(fields, "posted_at", posting.get("publishedAt"))
        _put(fields, "external_id", posting.get("id"))
        _put(fields, "apply_url", posting.get("applyUrl"))
        _add_compensation(fields, posting.get("compensation"))

        yield fields
