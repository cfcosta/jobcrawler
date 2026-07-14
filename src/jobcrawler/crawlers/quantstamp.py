import json


SITE = "https://api.ashbyhq.com/posting-api/job-board/quantstamp?includeCompensation=true"
EXTRA_HOSTS = []


def _set_if_present(fields, name, value):
    if value is not None and value != "":
        fields[name] = value


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs", []):
        primary_location = posting["location"]
        locations = [primary_location]
        for secondary in posting.get("secondaryLocations") or []:
            if isinstance(secondary, dict):
                value = secondary.get("location")
            else:
                value = secondary
            if value:
                locations.append(value)

        fields = {
            "url": posting["jobUrl"],
            "title": posting["title"],
            "company": "Quantstamp",
            "location": primary_location,
            "description": posting["descriptionPlain"],
            "source": "ashby",
            "locations": locations,
        }
        _set_if_present(fields, "employment_type", posting.get("employmentType"))
        _set_if_present(fields, "workplace_type", posting.get("workplaceType"))
        _set_if_present(fields, "department", posting.get("department"))
        _set_if_present(fields, "posted_at", posting.get("publishedAt"))
        _set_if_present(fields, "external_id", posting.get("id"))
        _set_if_present(fields, "apply_url", posting.get("applyUrl"))

        compensation = posting.get("compensation") or {}
        _set_if_present(
            fields,
            "salary",
            compensation.get("scrapeableCompensationSalarySummary"),
        )

        monetary = [
            component
            for component in compensation.get("summaryComponents") or []
            if isinstance(component, dict)
            and component.get("currencyCode")
            and (
                component.get("minValue") is not None
                or component.get("maxValue") is not None
            )
        ]
        if len(monetary) == 1:
            component = monetary[0]
            _set_if_present(fields, "salary_min", component.get("minValue"))
            _set_if_present(fields, "salary_max", component.get("maxValue"))
            _set_if_present(
                fields, "salary_currency", component.get("currencyCode")
            )
            _set_if_present(fields, "salary_period", component.get("interval"))

        yield Job(**fields)
