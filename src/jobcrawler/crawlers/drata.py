import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/drata?includeCompensation=true'
EXTRA_HOSTS = []


def _present(value):
    return value if value not in (None, "") else None


def crawl(ctx):
    response = ctx.fetch(SITE)
    raw = response.text if hasattr(response, "text") else response
    data = json.loads(raw)

    for posting in data.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        title = _present(posting.get("title"))
        location = _present(posting.get("location"))
        description = _present(posting.get("descriptionPlain"))
        url = _present(posting.get("jobUrl"))
        if not all((title, location, description, url)):
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Drata",
            "location": location,
            "description": description,
            "source": SITE,
        }

        optional = {
            "employment_type": _present(posting.get("employmentType")),
            "workplace_type": _present(posting.get("workplaceType")),
            "department": _present(posting.get("department")),
            "posted_at": _present(posting.get("publishedAt")),
            "external_id": _present(posting.get("id")),
            "apply_url": _present(posting.get("applyUrl")),
        }
        fields.update({key: value for key, value in optional.items() if value is not None})

        locations = [location]
        for secondary in posting.get("secondaryLocations") or []:
            secondary_location = secondary.get("location") if isinstance(secondary, dict) else secondary
            if secondary_location not in (None, "") and secondary_location not in locations:
                locations.append(secondary_location)
        if len(locations) > 1:
            fields["locations"] = locations

        compensation = posting.get("compensation") or {}
        components = compensation.get("summaryComponents") or []
        salary_components = [
            component for component in components
            if component.get("minValue") is not None
            or component.get("maxValue") is not None
        ]
        if salary_components:
            component = salary_components[0]
            salary_fields = {
                "salary_min": component.get("minValue"),
                "salary_max": component.get("maxValue"),
                "salary_currency": _present(component.get("currencyCode")),
                "salary_period": _present(component.get("interval")),
            }
            fields.update({
                key: value for key, value in salary_fields.items()
                if value is not None
            })
            printed_salary = _present(
                compensation.get("compensationTierSummary")
                or compensation.get("scrapeableCompensationSalarySummary")
            )
            if printed_salary is not None:
                fields["salary"] = printed_salary

        yield fields
