import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/braintrust?includeCompensation=true"
EXTRA_HOSTS = []


def _put(fields, key, value):
    if value is not None and value != "":
        fields[key] = value


def crawl(ctx):
    response = ctx.fetch(SITE)
    data = json.loads(response)

    for posting in data.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        location = posting.get("location")
        locations = []
        if location:
            locations.append(location)
        for secondary in posting.get("secondaryLocations") or []:
            value = secondary.get("location")
            if value and value not in locations:
                locations.append(value)

        fields = {
            "url": posting.get("jobUrl"),
            "title": posting.get("title"),
            "company": "Braintrust",
            "location": location,
            "description": posting.get("descriptionPlain"),
            "source": SITE,
        }
        if locations:
            fields["locations"] = locations

        _put(fields, "external_id", posting.get("id"))
        _put(fields, "apply_url", posting.get("applyUrl"))
        _put(fields, "employment_type", posting.get("employmentType"))
        _put(fields, "department", posting.get("department") or posting.get("team"))
        _put(fields, "posted_at", posting.get("publishedAt"))

        workplace = posting.get("workplaceType")
        if not workplace and posting.get("isRemote") is True:
            workplace = "Remote"
        _put(fields, "workplace_type", workplace)

        compensation = posting.get("compensation") or {}
        _put(fields, "salary", compensation.get("scrapeableCompensationSalarySummary"))

        salary_components = []
        for component in compensation.get("summaryComponents") or []:
            if component.get("compensationType") == "Salary":
                salary_components.append(component)

        if len(salary_components) == 1:
            salary_component = salary_components[0]
            _put(fields, "salary_min", salary_component.get("minValue"))
            _put(fields, "salary_max", salary_component.get("maxValue"))
            _put(fields, "salary_currency", salary_component.get("currencyCode"))
            _put(fields, "salary_period", salary_component.get("interval"))

        yield fields
