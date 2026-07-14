import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/mural?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    response = ctx.fetch(SITE)
    data = json.loads(response)

    for posting in data.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        primary_location = posting.get("location")
        locations = []
        if primary_location:
            locations.append(primary_location)
        for secondary in posting.get("secondaryLocations") or []:
            value = secondary.get("location") if isinstance(secondary, dict) else secondary
            if value:
                locations.append(value)

        fields = {
            "source": SITE,
            "url": posting.get("jobUrl"),
            "title": posting.get("title"),
            "company": "Mural",
            "location": primary_location,
            "description": posting.get("descriptionPlain"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": posting.get("employmentType"),
            "department": posting.get("department") or posting.get("team"),
            "posted_at": posting.get("publishedAt"),
        }

        if locations:
            fields["locations"] = locations

        workplace_type = posting.get("workplaceType")
        if workplace_type:
            fields["workplace_type"] = workplace_type
        elif posting.get("isRemote") is True:
            fields["workplace_type"] = "Remote"

        yield {key: value for key, value in fields.items() if value is not None}
