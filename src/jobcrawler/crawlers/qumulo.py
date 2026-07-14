import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/qumulo?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for posting in payload.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        title = posting.get("title")
        location = posting.get("location")
        description = posting.get("descriptionPlain")
        url = posting.get("jobUrl")
        if not title or not location or not description or not url:
            continue

        fields = {
            "url": url,
            "source": SITE,
            "title": title,
            "company": "Qumulo",
            "location": location,
            "description": description,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department") or posting.get("team"),
            "posted_at": posting.get("publishedAt"),
        }

        secondary = [
            item.get("location")
            for item in posting.get("secondaryLocations", [])
            if item.get("location")
        ]
        if secondary:
            fields["locations"] = [location] + secondary

        yield {key: value for key, value in fields.items() if value is not None}
