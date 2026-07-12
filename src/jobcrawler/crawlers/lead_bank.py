import json


SITE = 'https://api.ashbyhq.com/posting-api/job-board/leadbank?includeCompensation=true'
EXTRA_HOSTS = []


def _locations(item):
    values = []
    primary = item.get("location")
    if primary:
        values.append(primary)

    for entry in item.get("secondaryLocations") or []:
        value = entry.get("location") if isinstance(entry, dict) else entry
        if value and value not in values:
            values.append(value)

    return values


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for item in data.get("jobs", []):
        if item.get("isListed") is False:
            continue

        title = item.get("title")
        location = item.get("location")
        url = item.get("jobUrl")
        description = item.get("descriptionPlain")
        if not (title and location and url and description):
            continue

        department = item.get("department")
        team = item.get("team")
        if department and team and team != department:
            department = department + " / " + team
        elif not department:
            department = team

        fields = dict(
            url=url,
            title=title,
            company="Lead Bank",
            location=location,
            description=description,
            source=SITE,
            employment_type=item.get("employmentType"),
            workplace_type=item.get("workplaceType"),
            department=department,
            posted_at=item.get("publishedAt"),
            external_id=item.get("id"),
            apply_url=item.get("applyUrl"),
        )

        locations = _locations(item)
        if locations:
            fields["locations"] = locations

        yield fields
