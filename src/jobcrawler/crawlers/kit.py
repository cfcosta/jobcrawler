import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/kit?includeCompensation=true"
EXTRA_HOSTS = []


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        title = posting.get("title")
        description = posting.get("descriptionPlain")
        url = posting.get("jobUrl")
        location = posting.get("location")
        if not title or not description or not url or not location:
            continue

        job = {
            "url": url,
            "title": title,
            "company": "Kit",
            "location": location,
            "description": description,
            "source": SITE,
        }

        locations = [location]
        for secondary in posting.get("secondaryLocations") or []:
            if isinstance(secondary, dict):
                value = secondary.get("location")
            else:
                value = secondary
            if value and value not in locations:
                locations.append(value)
        job["locations"] = locations

        optional_fields = {
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        for key, value in optional_fields.items():
            if value is not None and value != "":
                job[key] = value

        compensation = posting.get("compensation") or {}
        salary_components = [
            component
            for component in compensation.get("summaryComponents") or []
            if component.get("compensationType") == "Salary"
        ]
        if len(salary_components) == 1:
            component = salary_components[0]
            salary_fields = {
                "salary_min": component.get("minValue"),
                "salary_max": component.get("maxValue"),
                "salary_currency": component.get("currencyCode"),
                "salary_period": component.get("interval"),
            }
            for key, value in salary_fields.items():
                if value is not None and value != "":
                    job[key] = value

            printed_salary = compensation.get("compensationTierSummary")
            if printed_salary:
                job["salary"] = printed_salary

        yield job
