import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/stream?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    response = ctx.fetch(SITE)
    payload = json.loads(response)

    for item in payload.get("jobs", []):
        if not item.get("isListed", True):
            continue

        location = item.get("location")
        locations = []
        if location:
            locations.append(location)
        for secondary in item.get("secondaryLocations") or []:
            value = secondary.get("location")
            if value:
                locations.append(value)

        fields = {
            "url": item.get("jobUrl"),
            "title": item.get("title"),
            "company": "Stream",
            "location": location,
            "description": item.get("descriptionPlain"),
            "source": SITE,
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "department": item.get("department"),
            "posted_at": item.get("publishedAt"),
            "expires_at": item.get("expiresAt"),
            "external_id": item.get("id"),
            "apply_url": item.get("applyUrl"),
        }
        if locations:
            fields["locations"] = locations

        yield {key: value for key, value in fields.items() if value is not None}
