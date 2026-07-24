import json
from html import unescape

SITE = "https://api.gem.com/job_board/v0/dydx/job_posts/"
EXTRA_HOSTS = []


def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))

    for posting in postings:
        location = (posting.get("location") or {}).get("name")
        description = unescape(posting.get("content_plain") or "").strip()
        url = posting.get("absolute_url")

        fields = {
            "url": url,
            "title": posting.get("title"),
            "company": "dYdX",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(posting["id"]) if posting.get("id") is not None else None,
            "apply_url": url,
            "employment_type": posting.get("employment_type"),
            "workplace_type": posting.get("location_type"),
            "posted_at": posting.get("first_published_at"),
        }

        locations = [
            office.get("name")
            for office in posting.get("offices", [])
            if office.get("name")
        ]
        if locations:
            fields["locations"] = locations

        departments = [
            department.get("name")
            for department in posting.get("departments", [])
            if department.get("name")
        ]
        if departments:
            fields["department"] = ", ".join(departments)

        yield {
            key: value
            for key, value in fields.items()
            if value not in (None, "")
        }
