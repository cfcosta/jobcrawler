
import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/netbird?includeCompensation=true"
EXTRA_HOSTS = []


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs", []):
        if posting.get("isListed") is not True:
            continue

        title = posting.get("title")
        description = posting.get("descriptionPlain")
        location = posting.get("location")
        url = posting.get("jobUrl")
        if not all((title, description, location, url)):
            continue

        job = {
            "url": url,
            "title": title,
            "company": "NetBird",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": posting.get("id"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department") or posting.get("team"),
            "posted_at": posting.get("publishedAt"),
            "apply_url": posting.get("applyUrl"),
        }

        secondary = [
            item.get("location")
            for item in posting.get("secondaryLocations", [])
            if isinstance(item, dict) and item.get("location")
        ]
        if secondary:
            job["locations"] = [location] + secondary

        yield {
            key: value
            for key, value in job.items()
            if value is not None and value != ""
        }
