import json

SITE = 'https://api.gem.com/job_board/v0/codesignal/job_posts/'
EXTRA_HOSTS = []


def _names(items):
    names = []
    for item in items or []:
        if isinstance(item, dict):
            name = item.get("name")
            if name:
                names.append(name)
    return names


def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))

    for posting in postings:
        location_data = posting.get("location") or {}
        location = location_data.get("name")
        description = posting.get("content_plain")
        url = posting.get("absolute_url")
        title = posting.get("title")

        if not all((url, title, location, description)):
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "CodeSignal",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(posting["id"]) if posting.get("id") is not None else None,
            "apply_url": url,
            "employment_type": posting.get("employment_type"),
            "workplace_type": posting.get("location_type"),
            "posted_at": posting.get("first_published_at"),
        }

        office_names = _names(posting.get("offices"))
        if office_names:
            fields["locations"] = office_names

        departments = _names(posting.get("departments"))
        if departments:
            fields["department"] = ", ".join(departments)

        yield {key: value for key, value in fields.items() if value is not None}
