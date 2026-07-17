import json
from jobcrawler.runtime.contract import Job

SITE = 'https://api.ashbyhq.com/posting-api/job-board/continue?includeCompensation=true'
EXTRA_HOSTS = []


def _value(mapping, *names):
    for name in names:
        value = mapping.get(name)
        if value is not None and value != "":
            return value
    return None


def _compensation_fields(posting):
    compensation = posting.get("compensation") or {}
    components = compensation.get("summaryComponents") or []
    numeric = [
        item for item in components
        if isinstance(item, dict)
        and (item.get("minValue") is not None or item.get("maxValue") is not None)
    ]
    currencies = {
        _value(item, "currencyCode", "currency")
        for item in numeric
        if _value(item, "currencyCode", "currency")
    }
    periods = {
        _value(item, "interval", "period")
        for item in numeric
        if _value(item, "interval", "period")
    }

    fields = {}
    printed = _value(
        compensation,
        "compensationTierSummary",
        "summary",
        "scrapeableCompensationSalarySummary",
    )
    if printed:
        fields["salary"] = printed

    # A single coherent range maps safely to the host's salary quartet.
    if numeric and len(currencies) <= 1 and len(periods) <= 1:
        minimums = [item["minValue"] for item in numeric if item.get("minValue") is not None]
        maximums = [item["maxValue"] for item in numeric if item.get("maxValue") is not None]
        if minimums:
            fields["salary_min"] = min(minimums)
        if maximums:
            fields["salary_max"] = max(maximums)
        if currencies:
            fields["salary_currency"] = next(iter(currencies))
        if periods:
            fields["salary_period"] = next(iter(periods))
    return fields


def crawl(ctx):
    try:
        response = ctx.fetch(SITE)
    except Exception:
        # Ashby removes retired boards with HTTP 404.
        return

    if isinstance(response, str):
        data = json.loads(response)
    elif isinstance(response, (bytes, bytearray)):
        data = json.loads(response.decode("utf-8"))
    elif hasattr(response, "json"):
        data = response.json()
    else:
        data = response

    for posting in data.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        title = posting.get("title")
        description = posting.get("descriptionPlain")
        url = posting.get("jobUrl")
        location = posting.get("location")
        if not title or not description or not url or not location:
            continue

        locations = [location]
        for secondary in posting.get("secondaryLocations") or []:
            value = secondary.get("location") if isinstance(secondary, dict) else secondary
            if value and value not in locations:
                locations.append(value)

        fields = {
            "url": url,
            "title": title,
            "company": "Continue",
            "location": location,
            "locations": locations,
            "description": description,
        }
        optional = {
            "department": _value(posting, "department", "team"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "posted_at": _value(posting, "publishedAt", "postedAt"),
            "expires_at": posting.get("expiresAt"),
            "external_id": _value(posting, "id", "jobId"),
            "apply_url": posting.get("applyUrl"),
        }
        fields.update({key: value for key, value in optional.items() if value is not None and value != ""})
        fields.update(_compensation_fields(posting))
        yield Job(**fields)
