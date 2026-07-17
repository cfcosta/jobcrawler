import json

from jobcrawler.runtime.contract import Job

SITE = 'https://api.ashbyhq.com/posting-api/job-board/mapbox?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        location = posting.get("location")
        locations = []
        if location:
            locations.append(location)
        for secondary in posting.get("secondaryLocations") or []:
            value = secondary.get("location")
            if value:
                locations.append(value)

        workplace_type = posting.get("workplaceType")
        if not workplace_type and posting.get("isRemote") is True:
            workplace_type = "Remote"

        fields = {
            "url": posting.get("jobUrl"),
            "title": posting.get("title"),
            "company": "Mapbox",
            "location": location,
            "description": posting.get("descriptionPlain"),
            "source": SITE,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": workplace_type,
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
        }
        if locations:
            fields["locations"] = locations

        compensation = posting.get("compensation") or {}
        components = compensation.get("summaryComponents") or []
        component = next(
            (
                item for item in components
                if item.get("minValue") is not None
                or item.get("maxValue") is not None
            ),
            None,
        )
        if component:
            fields["salary_min"] = component.get("minValue")
            fields["salary_max"] = component.get("maxValue")
            fields["salary_currency"] = component.get("currencyCode")
            fields["salary_period"] = component.get("interval")

        salary_text = (
            compensation.get("scrapeableCompensationSalarySummary")
            or compensation.get("compensationTierSummary")
        )
        if salary_text:
            fields["salary"] = salary_text

        yield Job(**{key: value for key, value in fields.items() if value is not None})
