
import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/windmill?includeCompensation=true"
EXTRA_HOSTS = []


def crawl(ctx):
    response = ctx.fetch(SITE)
    if isinstance(response, str):
        data = json.loads(response)
    else:
        text = response.text()
        data = json.loads(text)

    for item in data.get("jobs", []):
        if not item.get("isListed", True):
            continue

        job = {
            "url": item["jobUrl"],
            "title": item["title"],
            "company": "Windmill",
            "location": item["location"],
            "description": item["descriptionPlain"],
            "source": SITE,
            "external_id": item["id"],
        }

        optional = {
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "department": item.get("department") or item.get("team"),
            "posted_at": item.get("publishedAt"),
            "apply_url": item.get("applyUrl"),
        }
        job.update({key: value for key, value in optional.items() if value is not None})

        locations = [item["location"]]
        for secondary in item.get("secondaryLocations") or []:
            value = secondary.get("location") if isinstance(secondary, dict) else secondary
            if value:
                locations.append(value)
        job["locations"] = locations

        if not job.get("workplace_type") and item.get("isRemote") is True:
            job["workplace_type"] = "Remote"

        components = (item.get("compensation") or {}).get("summaryComponents") or []
        for component in components:
            if component.get("minValue") is None and component.get("maxValue") is None:
                continue
            job["salary_min"] = component.get("minValue")
            job["salary_max"] = component.get("maxValue")
            job["salary_currency"] = component.get("currencyCode")
            job["salary_period"] = component.get("interval")
            break

        yield job
