
import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/CloudZero?includeCompensation=true"
EXTRA_HOSTS = []


def _json_payload(response):
    if isinstance(response, dict):
        return response
    if isinstance(response, bytes):
        return json.loads(response.decode("utf-8"))
    if isinstance(response, str):
        return json.loads(response)
    if hasattr(response, "json"):
        value = response.json()
        return value
    text = response.text
    return json.loads(text() if callable(text) else text)


def crawl(ctx):
    data = _json_payload(ctx.fetch(SITE))

    for posting in data.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        job = {
            "url": posting.get("jobUrl"),
            "title": posting.get("title"),
            "company": "CloudZero",
            "location": posting.get("location"),
            "description": posting.get("descriptionPlain"),
            "source": SITE,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": posting.get("employmentType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
        }

        workplace = posting.get("workplaceType")
        if workplace:
            job["workplace_type"] = workplace
        elif posting.get("isRemote") is True:
            job["workplace_type"] = "Remote"

        locations = []
        if posting.get("location"):
            locations.append(posting["location"])
        for secondary in posting.get("secondaryLocations") or []:
            name = secondary.get("location") if isinstance(secondary, dict) else secondary
            if name:
                locations.append(name)
        if locations:
            job["locations"] = locations

        compensation = posting.get("compensation") or {}
        salary_parts = [
            component
            for component in compensation.get("summaryComponents") or []
            if component.get("compensationType") == "Salary"
        ]
        if len(salary_parts) == 1:
            component = salary_parts[0]
            if component.get("minValue") is not None:
                job["salary_min"] = component["minValue"]
            if component.get("maxValue") is not None:
                job["salary_max"] = component["maxValue"]
            if component.get("currencyCode"):
                job["salary_currency"] = component["currencyCode"]
            if component.get("interval"):
                job["salary_period"] = component["interval"]

            printed = compensation.get("scrapeableCompensationSalarySummary")
            if printed:
                job["salary"] = printed

        yield {
            key: value
            for key, value in job.items()
            if value is not None and value != ""
        }
