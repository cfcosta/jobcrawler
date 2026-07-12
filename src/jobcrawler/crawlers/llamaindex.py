from json import loads

SITE = "https://api.ashbyhq.com/posting-api/job-board/llamaindex?includeCompensation=true"
EXTRA_HOSTS = []


def crawl(ctx):
    response = ctx.fetch(SITE)
    try:
        data = response.json()
    except AttributeError:
        data = loads(response)

    for posting in data.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        title = posting.get("title")
        description = posting.get("descriptionPlain")
        location = posting.get("location")
        url = posting.get("jobUrl") or posting.get("applyUrl")
        if not all((title, description, location, url)):
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "LlamaIndex",
            "location": location,
            "description": description,
            "source": SITE,
        }

        secondary = posting.get("secondaryLocations") or []
        locations = [location]
        for item in secondary:
            value = item.get("location") if isinstance(item, dict) else item
            if value and value not in locations:
                locations.append(value)
        if len(locations) > 1:
            fields["locations"] = locations

        optional = {
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department") or posting.get("team"),
            "posted_at": posting.get("publishedAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        fields.update({
            key: value
            for key, value in optional.items()
            if value is not None and value != ""
        })

        compensation = posting.get("compensation") or {}
        salary_components = [
            component
            for component in compensation.get("summaryComponents", [])
            if component.get("compensationType") == "Salary"
        ]
        currencies = {
            component.get("currencyCode")
            for component in salary_components
            if component.get("currencyCode")
        }
        if salary_components and len(currencies) <= 1:
            component = salary_components[0]
            salary_fields = {
                "salary": compensation.get("scrapeableCompensationSalarySummary")
                or component.get("summary"),
                "salary_min": component.get("minValue"),
                "salary_max": component.get("maxValue"),
                "salary_currency": component.get("currencyCode"),
                "salary_period": component.get("interval"),
            }
            fields.update({
                key: value
                for key, value in salary_fields.items()
                if value is not None and value != ""
            })

        yield fields
