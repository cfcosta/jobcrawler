
import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/kombo?includeCompensation=true"
EXTRA_HOSTS = []


def _nonempty(value):
    return value if value not in (None, "") else None


def crawl(ctx):
    last_error = None
    for _ in range(3):
        try:
            body = ctx.fetch(SITE)
            break
        except Exception as error:
            last_error = error
    else:
        raise last_error

    data = json.loads(body)

    for record in data.get("jobs", []):
        if not record.get("isListed"):
            continue

        location = _nonempty(record.get("location"))
        title = _nonempty(record.get("title"))
        description = _nonempty(record.get("descriptionPlain"))
        url = _nonempty(record.get("jobUrl"))
        if not all((location, title, description, url)):
            continue

        fields = {
            "url": url,
            "source": SITE,
            "title": title,
            "company": "Kombo",
            "location": location,
            "description": description,
            "external_id": _nonempty(record.get("id")),
            "apply_url": _nonempty(record.get("applyUrl")),
            "employment_type": _nonempty(record.get("employmentType")),
            "workplace_type": _nonempty(record.get("workplaceType")),
            "department": _nonempty(record.get("department")),
            "posted_at": _nonempty(record.get("publishedAt")),
        }

        secondary = record.get("secondaryLocations") or []
        locations = [location]
        for item in secondary:
            secondary_location = (
                item.get("location") if isinstance(item, dict) else item
            )
            if secondary_location and secondary_location not in locations:
                locations.append(secondary_location)
        fields["locations"] = locations

        compensation = record.get("compensation") or {}
        salary_text = _nonempty(
            compensation.get("scrapeableCompensationSalarySummary")
        )
        salary_components = [
            component
            for component in compensation.get("summaryComponents", [])
            if component.get("compensationType") == "Salary"
            and component.get("currencyCode")
            and (
                component.get("minValue") is not None
                or component.get("maxValue") is not None
            )
        ]

        if salary_text:
            fields["salary"] = salary_text

        if len(salary_components) == 1:
            component = salary_components[0]
            fields["salary_min"] = component.get("minValue")
            fields["salary_max"] = component.get("maxValue")
            fields["salary_currency"] = component.get("currencyCode")
            fields["salary_period"] = component.get("interval")

        yield {
            key: value
            for key, value in fields.items()
            if value is not None
        }
