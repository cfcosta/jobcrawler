import html
import json
import re


SITE = 'https://api.ashbyhq.com/posting-api/job-board/openobserve-careers?includeCompensation=true'
EXTRA_HOSTS = []


def _html_to_text(value):
    value = re.sub(
        r"(?i)<(?:br\s*/?|/p|/div|/li|/h[1-6]|/section|/tr)>",
        "\n",
        value or "",
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    lines = (" ".join(line.split()) for line in value.splitlines())
    return "\n".join(line for line in lines if line)


def _description(item):
    plain = item.get("descriptionPlain")
    if plain and plain.strip():
        return plain.strip()
    return _html_to_text(item.get("descriptionHtml"))


def _compensation(item):
    compensation = item.get("compensation") or {}
    result = {}

    printed = (
        compensation.get("scrapeableCompensationSalarySummary")
        or compensation.get("compensationTierSummary")
    )
    if printed:
        result["salary"] = printed

    components = [
        component
        for component in compensation.get("summaryComponents") or []
        if component.get("minValue") is not None
        or component.get("maxValue") is not None
    ]
    currencies = {
        component.get("currencyCode")
        for component in components
        if component.get("currencyCode")
    }
    periods = {
        component.get("interval")
        for component in components
        if component.get("interval")
    }

    if components and len(currencies) <= 1 and len(periods) <= 1:
        minimums = [
            component["minValue"]
            for component in components
            if component.get("minValue") is not None
        ]
        maximums = [
            component["maxValue"]
            for component in components
            if component.get("maxValue") is not None
        ]
        if minimums:
            result["salary_min"] = min(minimums)
        if maximums:
            result["salary_max"] = max(maximums)
        if currencies:
            result["salary_currency"] = next(iter(currencies))
        if periods:
            result["salary_period"] = next(iter(periods))

    return result


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for item in payload.get("jobs", []):
        if item.get("isListed") is False:
            continue

        fields = {
            "url": item["jobUrl"],
            "title": item["title"],
            "company": "OpenObserve",
            "location": item["location"],
            "description": _description(item),
            "source": SITE,
        }

        optional = {
            "department": item.get("department"),
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "posted_at": item.get("publishedAt"),
            "external_id": item.get("id"),
            "apply_url": item.get("applyUrl"),
        }
        for key, value in optional.items():
            if value is not None and value != "":
                fields[key] = value

        secondary_locations = [
            entry.get("location")
            for entry in item.get("secondaryLocations") or []
            if entry.get("location")
        ]
        if secondary_locations:
            fields["locations"] = [item["location"]] + secondary_locations

        fields.update(_compensation(item))
        yield fields
