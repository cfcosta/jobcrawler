import json


SITE = "https://api.ashbyhq.com/posting-api/job-board/codat?includeCompensation=true"
EXTRA_HOSTS = []


def _set(fields, name, value):
    if value is not None and value != "":
        fields[name] = value


def crawl(ctx):
    response = ctx.fetch(SITE)
    payload = response.json() if hasattr(response, "json") else json.loads(response)

    for posting in payload.get("jobs", []):
        if posting.get("isListed") is not True:
            continue

        fields = {
            "source": SITE,
            "url": posting.get("jobUrl"),
            "title": posting.get("title"),
            "company": "Codat",
            "location": posting.get("location"),
            "description": posting.get("descriptionPlain"),
        }

        _set(fields, "apply_url", posting.get("applyUrl"))
        _set(fields, "external_id", posting.get("id"))
        _set(fields, "employment_type", posting.get("employmentType"))
        _set(fields, "department", posting.get("department"))
        _set(fields, "posted_at", posting.get("publishedAt"))

        workplace_type = posting.get("workplaceType")
        if workplace_type:
            fields["workplace_type"] = workplace_type
        elif posting.get("isRemote") is True:
            fields["workplace_type"] = "Remote"

        locations = []
        if posting.get("location"):
            locations.append(posting["location"])
        for secondary in posting.get("secondaryLocations") or []:
            secondary_location = secondary.get("location")
            if secondary_location:
                locations.append(secondary_location)
        if locations:
            fields["locations"] = locations

        compensation = posting.get("compensation") or {}
        _set(fields, "salary", compensation.get("compensationTierSummary"))
        for component in compensation.get("summaryComponents") or []:
            if component.get("compensationType") != "Salary":
                continue
            _set(fields, "salary_min", component.get("minValue"))
            _set(fields, "salary_max", component.get("maxValue"))
            _set(fields, "salary_currency", component.get("currencyCode"))
            _set(fields, "salary_period", component.get("interval"))
            break

        yield fields
