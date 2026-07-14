import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/localstack?includeCompensation=true'
EXTRA_HOSTS = []


def _clean(value):
    return value if value not in (None, "") else None


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))
    for posting in payload.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        location = _clean(posting.get("location"))
        secondary = [
            item.get("location")
            for item in (posting.get("secondaryLocations") or [])
            if item.get("location")
        ]
        locations = ([location] if location else []) + secondary

        job = {
            "url": posting.get("jobUrl"),
            "title": posting.get("title"),
            "company": "LocalStack",
            "location": location,
            "locations": locations or None,
            "description": posting.get("descriptionPlain"),
            "source": "Ashby",
            "department": _clean(posting.get("department")),
            "employment_type": _clean(posting.get("employmentType")),
            "workplace_type": _clean(posting.get("workplaceType")),
            "posted_at": _clean(posting.get("publishedAt")),
            "external_id": _clean(posting.get("id")),
            "apply_url": _clean(posting.get("applyUrl")),
        }

        compensation = posting.get("compensation") or {}
        if posting.get("shouldDisplayCompensationOnJobPostings"):
            job["salary"] = _clean(compensation.get("compensationTierSummary"))
            components = [
                component
                for component in (compensation.get("summaryComponents") or [])
                if component.get("compensationType") == "Salary"
            ]
            if len(components) == 1:
                component = components[0]
                job.update(
                    salary_min=component.get("minValue"),
                    salary_max=component.get("maxValue"),
                    salary_currency=_clean(component.get("currencyCode")),
                    salary_period=_clean(component.get("interval")),
                )

        yield {key: value for key, value in job.items() if value is not None}
