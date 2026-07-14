import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/cow-dao?includeCompensation=true'
EXTRA_HOSTS = []
COMPANY = "CoW DAO"


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for posting in payload.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        primary_location = posting.get("location")
        locations = []
        if primary_location:
            locations.append(primary_location)
        locations.extend(
            secondary["location"]
            for secondary in (posting.get("secondaryLocations") or [])
            if secondary.get("location")
        )

        kwargs = {
            "url": posting.get("jobUrl"),
            "title": posting.get("title"),
            "company": COMPANY,
            "location": primary_location,
            "description": (
                posting.get("descriptionPlain")
                or posting.get("descriptionHtml")
            ),
            "source": "ashby",
        }

        optional = {
            "locations": locations or None,
            "employment_type": posting.get("employmentType"),
            "workplace_type": (
                posting.get("workplaceType")
                or ("Remote" if posting.get("isRemote") else None)
            ),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
            "expires_at": posting.get("expiresAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        kwargs.update({
            key: value
            for key, value in optional.items()
            if value is not None and value != ""
        })

        compensation = posting.get("compensation") or {}
        printed_salary = (
            compensation.get("compensationTierSummary")
            or compensation.get("scrapeableCompensationSalarySummary")
        )
        if isinstance(printed_salary, str) and printed_salary:
            kwargs["salary"] = printed_salary

        for component in compensation.get("summaryComponents") or []:
            if component.get("compensationType") not in (None, "Salary"):
                continue
            salary_fields = {
                "salary_min": component.get("minValue"),
                "salary_max": component.get("maxValue"),
                "salary_currency": component.get("currencyCode"),
                "salary_period": component.get("interval"),
            }
            kwargs.update({
                key: value
                for key, value in salary_fields.items()
                if value is not None and value != ""
            })
            break

        yield kwargs
