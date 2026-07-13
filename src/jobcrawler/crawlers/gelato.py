import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/gelato?includeCompensation=true"
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
            if secondary_location:
                locations.append(secondary_location)

        fields = {
            "url": item.get("jobUrl"),
            "title": item.get("title"),
            "company": "Gelato",
            "location": primary_location,
            "description": item.get("descriptionPlain"),
            "source": SITE,
            "external_id": item.get("id"),
            "apply_url": item.get("applyUrl"),
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "department": item.get("department"),
            "posted_at": item.get("publishedAt"),
        }

        if len(locations) > 1:
            fields["locations"] = locations

        yield {
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        }
