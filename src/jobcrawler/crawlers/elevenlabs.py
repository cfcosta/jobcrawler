
import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/elevenlabs?includeCompensation=true"
EXTRA_HOSTS = []


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for item in data.get("jobs", []):
        if item.get("isListed") is False:
            continue

        primary_location = item.get("location")
        locations = []
        if primary_location:
            locations.append(primary_location)

        for secondary in item.get("secondaryLocations") or []:
            secondary_location = secondary.get("location")
            if secondary_location and secondary_location not in locations:
                locations.append(secondary_location)

        fields = {
            "source": SITE,
            "url": item.get("jobUrl"),
            "title": item.get("title"),
            "company": "ElevenLabs",
            "location": primary_location,
            "locations": locations or None,
            "description": item.get("descriptionPlain"),
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "department": item.get("department") or item.get("team"),
            "posted_at": item.get("publishedAt"),
            "external_id": item.get("id"),
            "apply_url": item.get("applyUrl"),
        }

        compensation = item.get("compensation") or {}
        components = compensation.get("summaryComponents") or []
        if len(components) == 1:
            component = components[0]
            fields["salary_min"] = component.get("minValue")
            fields["salary_max"] = component.get("maxValue")
            fields["salary_currency"] = component.get("currencyCode")
            fields["salary_period"] = component.get("interval")
            fields["salary"] = (
                compensation.get("compensationTierSummary")
                or compensation.get("scrapeableCompensationSalarySummary")
            )

        yield {
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        }
