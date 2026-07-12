import json


SITE = "https://api.gem.com/job_board/v0/modular/job_posts/"
EXTRA_HOSTS = []


def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))

    for posting in postings:
        office_names = [
            office["name"].strip()
            for office in posting.get("offices", [])
            if office.get("name") and office["name"].strip()
        ]
        location = (posting.get("location") or {}).get("name", "").strip()
        if not location:
            location = office_names[0] if office_names else "Remote"

        department_names = [
            department["name"].strip()
            for department in posting.get("departments", [])
            if department.get("name") and department["name"].strip()
        ]

        fields = {
            "url": posting["absolute_url"],
            "title": posting["title"].strip(),
            "company": "Modular",
            "location": location,
            "description": posting["content_plain"].strip(),
            "source": SITE,
            "external_id": str(posting["id"]),
            "apply_url": posting["absolute_url"],
        }

        optional = {
            "locations": office_names or None,
            "workplace_type": posting.get("location_type"),
            "employment_type": posting.get("employment_type"),
            "department": ", ".join(department_names) if department_names else None,
            "posted_at": posting.get("first_published_at"),
        }
        fields.update({key: value for key, value in optional.items() if value})
        yield fields
