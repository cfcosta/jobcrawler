import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/dave?includeCompensation=true'
EXTRA_HOSTS = []


def _present(value):
    return value if value is not None and value != "" else None


def crawl(ctx):
    raw = ctx.fetch(SITE)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    data = json.loads(raw)

    for posting in data.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        title = _present(posting.get("title"))
        description = _present(
            posting.get("descriptionPlain") or posting.get("descriptionHtml")
        )
        url = _present(posting.get("jobUrl"))
        location = _present(posting.get("location"))
        if not title or not description or not url or not location:
            continue

        values = {
            "url": url,
            "title": title.strip(),
            "company": "Dave",
            "location": location,
            "description": description,
            "source": SITE,
        }

        locations = [location]
        for secondary in posting.get("secondaryLocations") or []:
            secondary_location = (
                secondary.get("location") if isinstance(secondary, dict) else secondary
            )
            if secondary_location:
                locations.append(secondary_location)
        values["locations"] = locations

        optional = {
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        for key, value in optional.items():
            if _present(value) is not None:
                values[key] = value

        compensation = posting.get("compensation") or {}
        salary_components = [
            component
            for component in compensation.get("summaryComponents") or []
            if component.get("compensationType") == "Salary"
            and (
                component.get("minValue") is not None
                or component.get("maxValue") is not None
            )
        ]
        currencies = {
            component.get("currencyCode")
            for component in salary_components
            if component.get("currencyCode")
        }
        if salary_components and len(currencies) <= 1:
            component = salary_components[0]
            salary_text = compensation.get("scrapeableCompensationSalarySummary")
            if salary_text:
                values["salary"] = salary_text
            if component.get("minValue") is not None:
                values["salary_min"] = component["minValue"]
            if component.get("maxValue") is not None:
                values["salary_max"] = component["maxValue"]
            if component.get("currencyCode"):
                values["salary_currency"] = component["currencyCode"]
            if component.get("interval"):
                values["salary_period"] = component["interval"]

        yield values
