
import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/hebbia-ai?includeCompensation=true"
EXTRA_HOSTS = []


def crawl(ctx):
    response = ctx.fetch(SITE)
    if hasattr(response, "text"):
        response = response.text
    data = json.loads(response)

    for item in data.get("jobs", []):
        if item.get("isListed") is False:
            continue

        description = item.get("descriptionPlain")
        title = item.get("title")
        location = item.get("location")
        url = item.get("jobUrl")
        if not all((description, title, location, url)):
            continue

        job = {
            "url": url,
            "title": title,
            "company": "Hebbia",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": item.get("id"),
            "employment_type": item.get("employmentType"),
            "department": item.get("department") or item.get("team"),
            "posted_at": item.get("publishedAt"),
            "apply_url": item.get("applyUrl"),
        }

        locations = [location]
        locations.extend(
            secondary["location"]
            for secondary in item.get("secondaryLocations", [])
            if secondary.get("location")
        )
        job["locations"] = locations

        workplace_type = item.get("workplaceType")
        if workplace_type:
            job["workplace_type"] = workplace_type
        elif item.get("isRemote") is True:
            job["workplace_type"] = "Remote"

        compensation = item.get("compensation") or {}
        salary_text = (
            compensation.get("scrapeableCompensationSalarySummary")
            or compensation.get("compensationTierSummary")
        )
        if salary_text:
            job["salary"] = salary_text

        salary_components = [
            component
            for component in compensation.get("summaryComponents", [])
            if component.get("compensationType") == "Salary"
            and component.get("currencyCode")
        ]
        currencies = {component["currencyCode"] for component in salary_components}
        if salary_components and len(currencies) == 1:
            component = salary_components[0]
            if component.get("minValue") is not None:
                job["salary_min"] = component["minValue"]
            if component.get("maxValue") is not None:
                job["salary_max"] = component["maxValue"]
            job["salary_currency"] = component["currencyCode"]
            if component.get("interval"):
                job["salary_period"] = component["interval"]

        yield {key: value for key, value in job.items() if value is not None}
