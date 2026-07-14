import json


SITE = 'https://api.ashbyhq.com/posting-api/job-board/vantage?includeCompensation=true'
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

    printed = (
        compensation.get("scrapeableCompensationSalarySummary")
        or compensation.get("compensationTierSummary")
    )
    if printed:
        fields["salary"] = printed

    for component in compensation.get("summaryComponents") or []:
        if component.get("compensationType") != "Salary":
            continue
        if component.get("minValue") is not None:
            fields["salary_min"] = component["minValue"]
        if component.get("maxValue") is not None:
            fields["salary_max"] = component["maxValue"]
        if component.get("currencyCode"):
            fields["salary_currency"] = component["currencyCode"]
        if component.get("interval"):
            fields["salary_period"] = component["interval"]
        break

    return fields


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for item in data.get("jobs") or []:
        if item.get("isListed") is False:
            continue

        description = item.get("descriptionPlain")
        title = item.get("title")
        url = item.get("jobUrl")
        location = item.get("location")
        if not title or not url or not location or not description:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Vantage",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": item.get("id"),
            "apply_url": item.get("applyUrl"),
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "department": item.get("department") or item.get("team"),
            "posted_at": item.get("publishedAt"),
        }

        locations = _locations(item)
        if locations:
            fields["locations"] = locations

        fields.update(_salary_fields(item))
        yield {key: value for key, value in fields.items()
               if value is not None and value != ""}
