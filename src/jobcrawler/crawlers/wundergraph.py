import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/wundergraph?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    response = ctx.fetch(SITE)
    payload = json.loads(response)

    for posting in payload.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        location = posting.get("location")
        data = {
            "title": posting.get("title"),
            "company": "WunderGraph",
            "location": location,
            "description": posting.get("descriptionPlain"),
            "source": SITE,
        }

        secondary = posting.get("secondaryLocations") or []
        locations = []
        if location:
            locations.append(location)
        for item in secondary:
            value = item.get("location") if isinstance(item, dict) else item
            if value:
                locations.append(value)
        if locations:
            data["locations"] = locations

        optional_fields = {
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department") or posting.get("team"),
            "posted_at": posting.get("publishedAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        for key, value in optional_fields.items():
            if value is not None and value != "":
                data[key] = value

        compensation = posting.get("compensation") or {}
        salary_component = next(
            (
                component
                for component in compensation.get("summaryComponents", [])
                if component.get("compensationType") == "Salary"
            ),
            None,
        )
        if salary_component:
            salary_fields = {
                "salary_min": salary_component.get("minValue"),
                "salary_max": salary_component.get("maxValue"),
                "salary_currency": salary_component.get("currencyCode"),
                "salary_period": salary_component.get("interval"),
                "salary": compensation.get("compensationTierSummary"),
            }
            for key, value in salary_fields.items():
                if value is not None and value != "":
                    data[key] = value

        data["url"] = posting.get("jobUrl")
        yield data
