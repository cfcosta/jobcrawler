"""Crawler for Benchling's Ashby job board (public posting-api, token `benchling`)."""

import json

SITE = (
    "https://api.ashbyhq.com/posting-api/job-board/benchling?includeCompensation=true"
)
COMPANY = "Benchling"
EXTRA_HOSTS = []


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue

        title = job.get("title")
        description = job.get("descriptionPlain") or ""
        url = job.get("jobUrl")
        location = job.get("location")
        if not (title and description and url and location):
            continue

        out = {
            "url": url,
            "title": title,
            "company": COMPANY,
            "location": location,
            "description": description,
            "source": SITE,
        }

        external_id = job.get("id")
        if external_id:
            out["external_id"] = external_id

        apply_url = job.get("applyUrl")
        if apply_url:
            out["apply_url"] = apply_url

        employment_type = job.get("employmentType")
        if employment_type:
            out["employment_type"] = employment_type

        workplace_type = job.get("workplaceType")
        if workplace_type:
            out["workplace_type"] = workplace_type

        department = job.get("department") or job.get("team")
        if department:
            out["department"] = department

        posted_at = job.get("publishedAt")
        if posted_at:
            out["posted_at"] = posted_at

        locations = []
        if location:
            locations.append(location)
        for secondary in job.get("secondaryLocations") or []:
            if isinstance(secondary, dict):
                secondary_location = secondary.get("location") or secondary.get("name")
            else:
                secondary_location = secondary
            if secondary_location and secondary_location not in locations:
                locations.append(secondary_location)
        if locations:
            out["locations"] = locations

        comp = job.get("compensation") or {}
        summary = comp.get("scrapeableCompensationSalarySummary") or comp.get(
            "compensationTierSummary"
        )
        if summary:
            out["salary"] = summary

        components = comp.get("summaryComponents") or []
        if len(components) == 1:
            component = components[0] or {}
            min_value = component.get("minValue")
            max_value = component.get("maxValue")
            currency = component.get("currencyCode")
            interval = component.get("interval")
            if min_value is not None:
                out["salary_min"] = min_value
            if max_value is not None:
                out["salary_max"] = max_value
            if currency:
                out["salary_currency"] = currency
            if interval:
                out["salary_period"] = interval

        yield out
