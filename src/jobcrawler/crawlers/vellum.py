
import json

from jobcrawler.runtime.contract import Job

SITE = "https://api.ashbyhq.com/posting-api/job-board/vellum?includeCompensation=true"
EXTRA_HOSTS = []


def _value(mapping, *keys):
    for key in keys:
        value = mapping.get(key)
        if value is not None and value != "":
            return value
    return None


def _compensation_fields(compensation):
    if not isinstance(compensation, dict) or not compensation:
        return {}

    result = {}
    salary_text = _value(compensation, "compensationTierSummary", "scrapeableCompensationSalarySummary")
    if salary_text:
        result["salary"] = salary_text

    components = compensation.get("summaryComponents") or []
    usable = []
    for component in components:
        if not isinstance(component, dict):
            continue
        minimum = component.get("minValue")
        maximum = component.get("maxValue")
        currency = component.get("currencyCode")
        interval = component.get("interval")
        if minimum is not None or maximum is not None:
            usable.append((minimum, maximum, currency, interval))

    # A single component can be represented without conflating currencies or periods.
    if len(usable) == 1:
        minimum, maximum, currency, interval = usable[0]
        if minimum is not None:
            result["salary_min"] = minimum
        if maximum is not None:
            result["salary_max"] = maximum
        if currency:
            result["salary_currency"] = currency
        if interval:
            result["salary_period"] = interval
    return result


def crawl(ctx):
    try:
        payload = json.loads(ctx.fetch(SITE))
    except Exception:
        # Ashby returns 404 after a board is removed; that represents no open roles.
        return

    for posting in payload.get("jobs", []):
        description = posting.get("descriptionPlain")
        if not description:
            continue

        primary_location = posting.get("location")
        locations = []
        if primary_location:
            locations.append(primary_location)
        for secondary in posting.get("secondaryLocations") or []:
            if isinstance(secondary, dict):
                location = secondary.get("location")
            else:
                location = secondary
            if location and location not in locations:
                locations.append(location)

        external_id = posting.get("id")
        job_url = posting.get("jobUrl") or posting.get("url")
        if not job_url and external_id:
            job_url = "https://jobs.ashbyhq.com/vellum/" + str(external_id)

        fields = {
            "url": job_url,
            "title": posting.get("title"),
            "company": "Vellum",
            "location": primary_location,
            "description": description,
            "source": "ashby",
        }
        optional = {
            "locations": locations or None,
            "department": _value(posting, "department", "team"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "posted_at": posting.get("publishedAt"),
            "external_id": str(external_id) if external_id is not None else None,
            "apply_url": posting.get("applyUrl"),
        }
        if not optional["workplace_type"] and posting.get("isRemote") is True:
            optional["workplace_type"] = "Remote"
        fields.update({key: value for key, value in optional.items() if value is not None})
        fields.update(_compensation_fields(posting.get("compensation")))
        yield Job(**fields)
