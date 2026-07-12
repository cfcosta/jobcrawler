
import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/infisical?includeCompensation=true'
EXTRA_HOSTS = []

def crawl(ctx):
    response = ctx.fetch(SITE)
    text = response.text if hasattr(response, "text") else response
    data = json.loads(text)

    for item in data.get("jobs", []):
        if not item.get("isListed", True):
            continue

        job = {
            "url": item.get("jobUrl"),
            "title": item.get("title"),
            "company": "Infisical",
            "location": item.get("location"),
            "description": item.get("descriptionPlain"),
            "source": SITE,
            "external_id": item.get("id"),
            "apply_url": item.get("applyUrl"),
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "department": item.get("department") or item.get("team"),
            "posted_at": item.get("publishedAt"),
        }

        locations = [item.get("location")]
        locations.extend(
            loc.get("location") for loc in item.get("secondaryLocations", [])
            if loc.get("location")
        )
        job["locations"] = [loc for loc in locations if loc]

        compensation = item.get("compensation") or {}
        salary_parts = [
            part for part in compensation.get("summaryComponents", [])
            if part.get("compensationType") == "Salary"
        ]
        if len(salary_parts) == 1:
            part = salary_parts[0]
            if part.get("minValue") is not None:
                job["salary_min"] = part["minValue"]
            if part.get("maxValue") is not None:
                job["salary_max"] = part["maxValue"]
            if part.get("currencyCode"):
                job["salary_currency"] = part["currencyCode"]
            if part.get("interval"):
                job["salary_period"] = part["interval"]
            printed = compensation.get("scrapeableCompensationSalarySummary")
            if printed:
                job["salary"] = printed

        yield {key: value for key, value in job.items() if value is not None}
