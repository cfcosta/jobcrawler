import json


SITE = 'https://api.ashbyhq.com/posting-api/job-board/anomalo?includeCompensation=true'
EXTRA_HOSTS = []


def _present(value):
    return value is not None and value != "" and value != []


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs", []):
        primary_location = posting.get("location")
        locations = []
        if _present(primary_location):
            locations.append(primary_location)

        for secondary in posting.get("secondaryLocations") or []:
            location = (
                secondary.get("location")
                if isinstance(secondary, dict)
                else secondary
            )
            if _present(location) and location not in locations:
                locations.append(location)

        fields = {
            "source": SITE,
            "url": posting.get("jobUrl"),
            "title": posting.get("title"),
            "company": "Anomalo",
            "location": primary_location,
            "description": posting.get("descriptionPlain"),
            "locations": locations or None,
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
            "expires_at": posting.get("expiresAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }

        compensation = posting.get("compensation") or {}
        printed_salary = (
            compensation.get("scrapeableCompensationSalarySummary")
            or compensation.get("compensationTierSummary")
        )
        if _present(printed_salary):
            fields["salary"] = printed_salary

        salary_components = [
            component
            for component in compensation.get("summaryComponents") or []
            if isinstance(component, dict)
            and str(component.get("compensationType", "salary")).lower()
            in ("salary", "base salary", "base")
        ]
        if len(salary_components) == 1:
            component = salary_components[0]
            fields["salary_min"] = component.get("minValue")
            fields["salary_max"] = component.get("maxValue")
            fields["salary_currency"] = component.get("currencyCode")
            fields["salary_period"] = component.get("interval")

        yield Job(**{
            key: value for key, value in fields.items() if _present(value)
        })
