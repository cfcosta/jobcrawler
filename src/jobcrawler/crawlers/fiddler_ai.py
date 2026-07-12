
import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/fiddler-ai?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    response = ctx.fetch(SITE)
    raw = response.text if hasattr(response, "text") else response
    data = json.loads(raw) if isinstance(raw, str) else raw

    for item in data.get("jobs", []):
        description = item.get("descriptionPlain")
        if not description:
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
            "url": item.get("jobUrl"),
            "title": item.get("title"),
            "company": "Fiddler AI",
            "location": primary_location,
            "description": description,
            "source": SITE,
            "external_id": item.get("id"),
            "apply_url": item.get("applyUrl"),
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "department": item.get("department") or item.get("team"),
            "posted_at": item.get("publishedAt"),
        }
        if locations:
            fields["locations"] = locations

        yield {key: value for key, value in fields.items() if value is not None}
