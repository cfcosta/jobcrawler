
import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/cerebras?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    response = ctx.fetch(SITE)
    if hasattr(response, "json"):
        data = response.json()
    else:
        raw = response.text if hasattr(response, "text") else response
        data = json.loads(raw)

    for item in data.get("jobs", []):
        if item.get("isListed") is not True:
            continue

        description = (item.get("descriptionPlain") or "").strip()
        location = item.get("location")
        if not description or not location:
            continue

        locations = [location]
        for secondary in item.get("secondaryLocations") or []:
            secondary_location = secondary.get("location")
            if secondary_location and secondary_location not in locations:
                locations.append(secondary_location)

        fields = {
            "url": item.get("jobUrl"),
            "title": item.get("title"),
            "company": "Cerebras Systems",
            "location": location,
            "locations": locations,
            "description": description,
            "source": SITE,
            "external_id": item.get("id"),
            "apply_url": item.get("applyUrl"),
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "department": item.get("department") or item.get("team"),
            "posted_at": item.get("publishedAt"),
        }
        yield {key: value for key, value in fields.items() if value is not None}
