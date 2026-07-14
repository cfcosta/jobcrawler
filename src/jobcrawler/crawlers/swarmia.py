import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/swarmia?includeCompensation=true"
EXTRA_HOSTS = []


def _locations(job):
    values = []
    primary = job.get("location")
    if primary:
        values.append(primary)

    for secondary in job.get("secondaryLocations") or []:
        if isinstance(secondary, dict):
            value = secondary.get("location")
        else:
            value = secondary
        if value and value not in values:
            values.append(value)
    return values


def _salary_fields(job):
    compensation = job.get("compensation") or {}
    fields = {}

    printed = compensation.get("compensationTierSummary")
    if printed:
        fields["salary"] = printed

    for component in compensation.get("summaryComponents") or []:
        if component.get("compensationType") != "Salary":
            continue

        minimum = component.get("minValue")
        maximum = component.get("maxValue")
        currency = component.get("currencyCode")
        period = component.get("interval")
        if minimum is not None:
            fields["salary_min"] = minimum
        if maximum is not None:
            fields["salary_max"] = maximum
        if currency:
            fields["salary_currency"] = currency
        if period:
            fields["salary_period"] = period
        break

    return fields


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs") or []:
        if posting.get("isListed") is False:
            continue

        title = posting.get("title")
        description = posting.get("descriptionPlain")
        url = posting.get("jobUrl")
        location = posting.get("location")
        if not title or not description or not url or not location:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Swarmia",
            "location": location,
            "description": description,
            "source": SITE,
        }

        locations = _locations(posting)
        if locations:
            fields["locations"] = locations

        workplace_type = posting.get("workplaceType")
        if not workplace_type and posting.get("isRemote") is True:
            workplace_type = "Remote"

        optional = {
            "employment_type": posting.get("employmentType"),
            "workplace_type": workplace_type,
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        fields.update({key: value for key, value in optional.items() if value})
        fields.update(_salary_fields(posting))

        yield fields
