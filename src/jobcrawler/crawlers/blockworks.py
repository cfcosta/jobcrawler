import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/Blockworks?includeCompensation=true"
EXTRA_HOSTS = []


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for posting in payload.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        location = posting.get("location")
        description = posting.get("descriptionPlain")
        if not posting.get("title") or not location or not description or not posting.get("jobUrl"):
            continue

        fields = {
            "url": posting["jobUrl"],
            "title": posting["title"],
            "company": "Blockworks",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
        }

        locations = [location]
        for secondary in posting.get("secondaryLocations") or []:
            value = secondary.get("location") if isinstance(secondary, dict) else secondary
            if value:
                locations.append(value)
        fields["locations"] = locations

        compensation = posting.get("compensation") or {}
        salary_component = next(
            (
                component
                for component in compensation.get("summaryComponents") or []
                if component.get("compensationType") == "Salary"
            ),
            None,
        )
        if salary_component:
            fields.update({
                "salary": compensation.get("compensationTierSummary"),
                "salary_min": salary_component.get("minValue"),
                "salary_max": salary_component.get("maxValue"),
                "salary_currency": salary_component.get("currencyCode"),
                "salary_period": salary_component.get("interval"),
            })

        yield {key: value for key, value in fields.items() if value is not None}
