import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/nomic.ai?includeCompensation=true"
EXTRA_HOSTS = []


def _compensation_fields(job):
    compensation = job.get("compensation") or {}
    components = compensation.get("summaryComponents") or []
    for component in components:
        minimum = component.get("minValue")
        maximum = component.get("maxValue")
        currency = component.get("currencyCode")
        period = component.get("interval")
        if minimum is None and maximum is None:
            continue

        fields = {}
        if minimum is not None:
            fields["salary_min"] = minimum
        if maximum is not None:
            fields["salary_max"] = maximum
        if currency:
            fields["salary_currency"] = currency
        if period:
            fields["salary_period"] = period

        printed = (
            component.get("summary")
            or compensation.get("scrapeableCompensationSalarySummary")
            or compensation.get("compensationTierSummary")
        )
        if printed:
            fields["salary"] = printed
        return fields
    return {}


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    seen = set()

    for item in data.get("jobs", []):
        if item.get("isListed") is False:
            continue

        external_id = item.get("id")
        url = item.get("jobUrl")
        dedupe_key = external_id or url
        if not dedupe_key or dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        title = (item.get("title") or "").strip()
        description = item.get("descriptionPlain")
        location = item.get("location")
        if not title or not description or not location or not url:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Nomic AI",
            "location": location,
            "description": description,
            "source": SITE,
        }

        secondary = item.get("secondaryLocations") or []
        locations = [location]
        for entry in secondary:
            value = entry.get("location") if isinstance(entry, dict) else entry
            if value and value not in locations:
                locations.append(value)
        fields["locations"] = locations

        optional = {
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "department": item.get("department") or item.get("team"),
            "posted_at": item.get("publishedAt"),
            "external_id": external_id,
            "apply_url": item.get("applyUrl"),
        }
        fields.update({key: value for key, value in optional.items() if value})
        fields.update(_compensation_fields(item))

        yield fields
