import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/paymentology?includeCompensation=true"
EXTRA_HOSTS = []


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        primary_location = posting.get("location")
        locations = []
        if primary_location:
            locations.append(primary_location)
        for secondary in posting.get("secondaryLocations") or []:
            location = secondary.get("location")
            if location:
                locations.append(location)

        workplace_type = posting.get("workplaceType")
        if not workplace_type and posting.get("isRemote") is True:
            workplace_type = "Remote"

        fields = {
            "source": SITE,
            "url": posting.get("jobUrl"),
            "title": posting.get("title"),
            "company": "Paymentology",
            "location": primary_location,
            "locations": locations or None,
            "description": posting.get("descriptionPlain"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": workplace_type,
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        yield {key: value for key, value in fields.items() if value is not None}
