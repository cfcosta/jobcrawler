import json


SITE = 'https://api.gem.com/job_board/v0/productboard/job_posts/'
EXTRA_HOSTS = []


def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))

    for posting in postings:
        location = posting.get("location") or {}
        departments = posting.get("departments") or []
        offices = posting.get("offices") or []

        job = {
            "url": posting.get("absolute_url"),
            "title": posting.get("title"),
            "company": "Productboard",
            "location": location.get("name"),
            "description": posting.get("content_plain"),
            "source": SITE,
            "external_id": str(posting["id"]) if posting.get("id") is not None else None,
            "apply_url": posting.get("absolute_url"),
            "employment_type": posting.get("employment_type"),
            "workplace_type": posting.get("location_type"),
            "department": departments[0].get("name") if departments else None,
            "posted_at": posting.get("first_published_at"),
        }

        locations = [
            office["name"]
            for office in offices
            if office.get("name")
        ]
        if locations:
            job["locations"] = locations

        yield {key: value for key, value in job.items() if value is not None}
