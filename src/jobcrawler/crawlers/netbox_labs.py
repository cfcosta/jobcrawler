import json


SITE = "https://api.ashbyhq.com/posting-api/job-board/netboxlabs?includeCompensation=true"
EXTRA_HOSTS = []


def _salary_fields(job):
    compensation = job.get("compensation") or {}
    components = compensation.get("summaryComponents") or []

    for component in components:
        if component.get("compensationType") != "Salary":
            continue

        fields = {}
        salary_text = compensation.get("compensationTierSummary")
        if salary_text:
            fields["salary"] = salary_text

        mappings = (
            ("minValue", "salary_min"),
            ("maxValue", "salary_max"),
            ("currencyCode", "salary_currency"),
            ("interval", "salary_period"),
        )
        for source_key, job_key in mappings:
            value = component.get(source_key)
            if value is not None and value != "":
                fields[job_key] = value
        return fields

    return {}


def crawl(ctx):
    response = ctx.fetch(SITE)
    data = json.loads(response)

    for posting in data.get("jobs", []):
        if not posting.get("isListed"):
            continue

        location = posting.get("location")
        description = posting.get("descriptionPlain")
        title = posting.get("title")
        url = posting.get("jobUrl")
        if not all((location, description, title, url)):
            continue

        locations = [location]
        for secondary in posting.get("secondaryLocations") or []:
            secondary_location = secondary.get("location")
            if secondary_location:
                locations.append(secondary_location)

        fields = {
            "url": url,
            "title": title,
            "company": "NetBox Labs",
            "location": location,
            "locations": locations,
            "description": description,
            "source": "ashby",
        }

        optional = {
            "department": posting.get("department"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "posted_at": posting.get("publishedAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        fields.update({
            key: value
            for key, value in optional.items()
            if value is not None and value != ""
        })
        fields.update(_salary_fields(posting))

        yield fields
