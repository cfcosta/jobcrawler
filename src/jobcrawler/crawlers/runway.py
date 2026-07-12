
import json
import re

SITE = "https://api.ashbyhq.com/posting-api/job-board/runway?includeCompensation=true"
EXTRA_HOSTS = []


def _clean_title(title):
    # Remove decorative leading emoji while preserving title punctuation.
    return re.sub(r"^[^\w]+", "", title, flags=re.UNICODE).strip()


def _compensation_fields(job):
    if not job.get("shouldDisplayCompensationOnJobPostings"):
        return {}

    compensation = job.get("compensation") or {}
    components = compensation.get("summaryComponents") or []
    component = next(
        (
            item
            for item in components
            if item.get("minValue") is not None
            or item.get("maxValue") is not None
        ),
        None,
    )
    if not component:
        return {}

    fields = {}
    mapping = {
        "minValue": "salary_min",
        "maxValue": "salary_max",
        "currencyCode": "salary_currency",
        "interval": "salary_period",
    }
    for source_key, job_key in mapping.items():
        value = component.get(source_key)
        if value is not None and value != "":
            fields[job_key] = value

    printed = (
        compensation.get("scrapeableCompensationSalarySummary")
        or compensation.get("compensationTierSummary")
    )
    if printed:
        fields["salary"] = printed
    return fields


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for item in payload.get("jobs", []):
        if item.get("isListed") is False:
            continue

        description = item.get("descriptionPlain")
        title = item.get("title")
        location = item.get("location")
        url = item.get("jobUrl")
        if not all((description, title, location, url)):
            continue

        fields = {
            "url": url,
            "title": _clean_title(title),
            "company": "Runway",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": item.get("id"),
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "department": item.get("department"),
            "posted_at": item.get("publishedAt"),
            "apply_url": item.get("applyUrl"),
        }

        secondary = item.get("secondaryLocations") or []
        locations = [location]
        for entry in secondary:
            secondary_location = (
                entry.get("location") if isinstance(entry, dict) else entry
            )
            if secondary_location and secondary_location not in locations:
                locations.append(secondary_location)
        if secondary:
            fields["locations"] = locations

        if not fields.get("workplace_type") and item.get("isRemote"):
            fields["workplace_type"] = "Remote"

        fields.update(_compensation_fields(item))
        yield {key: value for key, value in fields.items() if value is not None}
