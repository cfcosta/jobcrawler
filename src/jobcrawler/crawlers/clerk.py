import json
from jobcrawler.runtime.contract import Job


SITE = "https://api.ashbyhq.com/posting-api/job-board/Clerk?includeCompensation=true"
EXTRA_HOSTS = []


def _text(value):
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return None


def _locations(item):
    values = []
    primary = _text(item.get("location"))
    if primary:
        values.append(primary)

    for entry in item.get("secondaryLocations") or []:
        if isinstance(entry, dict):
            value = _text(entry.get("location"))
        else:
            value = _text(entry)
        if value and value not in values:
            values.append(value)

    return values


def _compensation_fields(item):
    compensation = item.get("compensation")
    if not isinstance(compensation, dict):
        return {}

    result = {}
    printed = _text(compensation.get("compensationTierSummary"))
    if printed:
        result["salary"] = printed

    components = compensation.get("summaryComponents") or []
    ranges = []
    for component in components:
        if not isinstance(component, dict):
            continue
        minimum = component.get("minValue")
        maximum = component.get("maxValue")
        currency = _text(component.get("currencyCode"))
        period = _text(component.get("interval"))
        if minimum is None and maximum is None:
            continue
        ranges.append((minimum, maximum, currency, period))

    # A single range is unambiguous. Multiple components may represent
    # bonuses or equity and must not be combined into a fabricated range.
    if len(ranges) == 1:
        minimum, maximum, currency, period = ranges[0]
        if minimum is not None:
            result["salary_min"] = minimum
        if maximum is not None:
            result["salary_max"] = maximum
        if currency:
            result["salary_currency"] = currency
        if period:
            result["salary_period"] = period

    return result


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for item in payload.get("jobs") or []:
        if not isinstance(item, dict) or item.get("isListed") is False:
            continue

        title = _text(item.get("title"))
        description = _text(item.get("descriptionPlain"))
        url = _text(item.get("jobUrl")) or _text(item.get("applyUrl"))
        locations = _locations(item)
        location = _text(item.get("location"))

        if not title or not description or not url or not location:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Clerk",
            "location": location,
            "description": description,
        }

        if len(locations) > 1:
            fields["locations"] = locations

        optional = {
            "external_id": _text(item.get("id")),
            "department": _text(item.get("department")) or _text(item.get("team")),
            "employment_type": _text(item.get("employmentType")),
            "workplace_type": _text(item.get("workplaceType")),
            "posted_at": _text(item.get("publishedAt")),
            "apply_url": _text(item.get("applyUrl")),
        }
        for key, value in optional.items():
            if value is not None:
                fields[key] = value

        fields.update(_compensation_fields(item))
        yield Job(**fields)
