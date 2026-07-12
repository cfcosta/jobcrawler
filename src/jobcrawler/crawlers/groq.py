
import json

SITE = "https://api.gem.com/job_board/v0/groq/job_posts/"
EXTRA_HOSTS = []


def _nonempty(value):
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def crawl(ctx):
    response = ctx.fetch(SITE)
    records = json.loads(response)

    for record in records:
        title = _nonempty(record.get("title"))
        description = _nonempty(record.get("content_plain"))
        url = _nonempty(record.get("absolute_url"))
        if not title or not description or not url:
            continue

        office_names = []
        for office in record.get("offices") or []:
            name = _nonempty(office.get("name"))
            if name and name not in office_names:
                office_names.append(name)

        primary_location = _nonempty((record.get("location") or {}).get("name"))
        location_type = _nonempty(record.get("location_type"))
        location = primary_location or (
            office_names[0] if office_names else location_type
        )
        if (
            location
            and location_type
            and location_type.lower() not in location.lower()
        ):
            location = f"{location} ({location_type})"

        departments = []
        for department in record.get("departments") or []:
            name = _nonempty(department.get("name"))
            if name:
                departments.append(name)

        yield dict(
            source=SITE,
            url=url,
            title=title,
            company="Groq",
            location=location,
            locations=office_names or None,
            description=description,
            employment_type=_nonempty(record.get("employment_type")),
            workplace_type=location_type,
            department=", ".join(departments) if departments else None,
            posted_at=_nonempty(record.get("first_published_at")),
            external_id=_nonempty(record.get("id")),
            apply_url=url,
        )
