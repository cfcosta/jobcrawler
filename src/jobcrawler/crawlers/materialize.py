import json


SITE = 'https://api.ashbyhq.com/posting-api/job-board/materialize?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs", []):
        if not posting.get("isListed", True):
            continue

        job = {
            "url": posting["jobUrl"],
            "title": posting["title"].strip(),
            "company": "Materialize",
            "location": posting["location"].strip(),
            "description": posting["descriptionPlain"].strip(),
            "source": SITE,
        }

        optional_fields = {
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        job.update({
            key: value.strip() if isinstance(value, str) else value
            for key, value in optional_fields.items()
            if value is not None and (not isinstance(value, str) or value.strip())
        })

        yield job
