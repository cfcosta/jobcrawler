import json

from jobcrawler.runtime.contract import Job


SITE = 'https://api.ashbyhq.com/posting-api/job-board/mistral.ai?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs", []):
        if not posting.get("isListed", True):
            continue

        title = posting.get("title")
        location = posting.get("location")
        description = posting.get("descriptionPlain")
        url = posting.get("jobUrl")
        if not all((title, location, description, url)):
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Mistral AI",
            "location": location,
            "description": description,
            "source": SITE,
        }

        locations = [location]
        locations.extend(
            item["location"]
            for item in posting.get("secondaryLocations", [])
            if item.get("location")
        )
        fields["locations"] = locations

        optional_fields = {
            "employment_type": posting.get("employmentType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        fields.update({
            key: value
            for key, value in optional_fields.items()
            if value is not None and value != ""
        })

        workplace_type = posting.get("workplaceType")
        if workplace_type:
            fields["workplace_type"] = workplace_type
        elif posting.get("isRemote") is True:
            fields["workplace_type"] = "Remote"

        compensation = posting.get("compensation") or {}
        components = compensation.get("summaryComponents") or []
        if len(components) == 1:
            component = components[0]
            salary_fields = {
                "salary_min": component.get("minValue"),
                "salary_max": component.get("maxValue"),
                "salary_currency": component.get("currencyCode"),
                "salary_period": component.get("interval"),
            }
            fields.update({
                key: value
                for key, value in salary_fields.items()
                if value is not None and value != ""
            })

        salary = (
            compensation.get("scrapeableCompensationSalarySummary")
            or compensation.get("compensationTierSummary")
        )
        if salary:
            fields["salary"] = salary

        yield Job(**fields)
