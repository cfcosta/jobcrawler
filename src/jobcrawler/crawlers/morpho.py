import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/morpho?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    board = json.loads(ctx.fetch(SITE))
    for posting in board.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        url = posting.get("jobUrl")
        title = posting.get("title")
        location = posting.get("location")
        description = posting.get("descriptionPlain")
        if not all((url, title, location, description)):
            continue

        locations = [location]
        for secondary in posting.get("secondaryLocations") or []:
            secondary_location = secondary.get("location")
            if secondary_location:
                locations.append(secondary_location)

        fields = {
            "url": url,
            "title": title,
            "company": "Morpho",
            "location": location,
            "locations": locations,
            "description": description,
            "source": "ashby",
        }

        mappings = (
            ("employment_type", "employmentType"),
            ("workplace_type", "workplaceType"),
            ("department", "department"),
            ("posted_at", "publishedAt"),
            ("external_id", "id"),
            ("apply_url", "applyUrl"),
        )
        for output_name, source_name in mappings:
            value = posting.get(source_name)
            if value is not None and value != "":
                fields[output_name] = value

        compensation = posting.get("compensation") or {}
        components = compensation.get("summaryComponents") or []
        if len(components) == 1:
            component = components[0]
            salary_mappings = (
                ("salary_min", "minValue"),
                ("salary_max", "maxValue"),
                ("salary_currency", "currencyCode"),
                ("salary_period", "interval"),
            )
            for output_name, source_name in salary_mappings:
                value = component.get(source_name)
                if value is not None and value != "":
                    fields[output_name] = value

        salary = (
            compensation.get("compensationTierSummary")
            or compensation.get("scrapeableCompensationSalarySummary")
        )
        if salary:
            fields["salary"] = salary

        yield fields
