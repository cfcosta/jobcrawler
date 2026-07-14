import json


SITE = 'https://api.ashbyhq.com/posting-api/job-board/delinea?includeCompensation=true'
EXTRA_HOSTS = []


def _present(value):
    return value if value not in (None, "") else None


def crawl(ctx):
    response = ctx.fetch(SITE)
    data = json.loads(response)

    for posting in data.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        description = _present(posting.get("descriptionPlain"))
        if description is None:
            continue

        primary_location = _present(posting.get("location"))
        locations = []
        if primary_location is not None:
            locations.append(primary_location)

        for secondary in posting.get("secondaryLocations") or []:
            if isinstance(secondary, dict):
                secondary_location = _present(secondary.get("location"))
            else:
                secondary_location = _present(secondary)
            if secondary_location is not None and secondary_location not in locations:
                locations.append(secondary_location)

        fields = {
            "url": posting.get("jobUrl"),
            "title": posting.get("title"),
            "company": "Delinea",
            "source": SITE,
            "location": primary_location,
            "description": description,
            "external_id": _present(posting.get("id")),
            "apply_url": _present(posting.get("applyUrl")),
            "employment_type": _present(posting.get("employmentType")),
            "department": _present(posting.get("department")),
            "posted_at": _present(posting.get("publishedAt")),
        }

        if locations:
            fields["locations"] = locations

        workplace_type = _present(posting.get("workplaceType"))
        if workplace_type is not None:
            fields["workplace_type"] = workplace_type
        elif posting.get("isRemote") is True:
            fields["workplace_type"] = "Remote"

        compensation = posting.get("compensation") or {}
        salary_components = [
            component
            for component in compensation.get("summaryComponents") or []
            if component.get("compensationType") == "Salary"
        ]
        if len(salary_components) == 1:
            salary_component = salary_components[0]
            salary_min = salary_component.get("minValue")
            salary_max = salary_component.get("maxValue")
            salary_currency = _present(salary_component.get("currencyCode"))
            salary_period = _present(salary_component.get("interval"))

            if (
                salary_min is not None
                and salary_max is not None
                and salary_currency is not None
                and salary_period is not None
            ):
                fields["salary_min"] = salary_min
                fields["salary_max"] = salary_max
                fields["salary_currency"] = salary_currency
                fields["salary_period"] = salary_period

                printed_salary = _present(
                    compensation.get("scrapeableCompensationSalarySummary")
                )
                if printed_salary is not None:
                    fields["salary"] = printed_salary

        yield fields
