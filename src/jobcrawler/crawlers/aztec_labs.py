import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/aztec-labs?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    payload = ctx.fetch(SITE)
    if not isinstance(payload, str):
        payload = payload.text

    data = json.loads(payload)
    for posting in data.get("jobs", []):
        if not posting.get("isListed", True):
            continue

        title = posting.get("title")
        location = posting.get("location")
        description = posting.get("descriptionPlain")
        job_url = posting.get("jobUrl")
        if not title or not location or not description or not job_url:
            continue

        locations = [location]
        for secondary in posting.get("secondaryLocations") or []:
            value = secondary.get("location")
            if value and value not in locations:
                locations.append(value)

        item = {
            "url": job_url,
            "title": title,
            "company": "Aztec Labs",
            "location": location,
            "locations": locations,
            "description": description,
            "source": SITE,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": posting.get("employmentType"),
            "department": posting.get("department") or posting.get("team"),
            "posted_at": posting.get("publishedAt"),
        }

        workplace_type = posting.get("workplaceType")
        if workplace_type:
            item["workplace_type"] = workplace_type
        elif posting.get("isRemote") is True:
            item["workplace_type"] = "Remote"

        yield {key: value for key, value in item.items() if value is not None}
