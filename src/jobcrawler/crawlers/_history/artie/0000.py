import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/artie?includeCompensation=true"
EXTRA_HOSTS = []


def _salary_fields(job):
    if not job.get("shouldDisplayCompensationOnJobPostings"):
        return {}

    compensation = job.get("compensation") or {}
    for component in compensation.get("summaryComponents") or []:
        if component.get("compensationType") != "Salary":
            continue
        minimum = component.get("minValue")
        maximum = component.get("maxValue")
        currency = component.get("currencyCode")
        interval = component.get("interval")
        if minimum is None or maximum is None or not currency or not interval:
            continue

        fields = {
            "salary_min": minimum,
            "salary_max": maximum,
            "salary_currency": currency,
            "salary_period": interval,
        }
        printed = compensation.get("compensationTierSummary")
        if printed:
            fields["salary"] = printed
        return fields
    return {}


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs", []):
        if not posting.get("isListed"):
            continue

        location = posting.get("location")
        description = posting.get("descriptionPlain")
        item = {
            "url": posting.get("jobUrl"),
            "title": posting.get("title"),
            "company": "Artie",
            "location": location,
            "description": description,
            "external_id": posting.get("id"),
            "source": "ashby",
        }

        optional_fields = {
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
            "apply_url": posting.get("applyUrl"),
        }
        item.update({key: value for key, value in optional_fields.items() if value is not None})

        secondary = posting.get("secondaryLocations") or []
        locations = []
        if location:
            locations.append(location)
        locations.extend(
            entry["location"]
            for entry in secondary
            if isinstance(entry, dict) and entry.get("location")
        )
        if locations:
            item["locations"] = locations

        item.update(_salary_fields(posting))
        yield item
