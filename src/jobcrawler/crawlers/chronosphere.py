import json
import re
import html


SITE = 'https://api.ashbyhq.com/posting-api/job-board/chronospherejobs?includeCompensation=true'
EXTRA_HOSTS = []


def clean_html(value):
    value = re.sub(r"<(?:br|/p|/div|/li|/h[1-6])\\b[^>]*>", "\\n", value,
                   flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \\t\\f\\v]+", " ", value)
    value = re.sub(r" *\\n *", "\\n", value)
    return re.sub(r"\\n{3,}", "\\n\\n", value).strip()


def compensation_fields(item):
    compensation = item.get("compensation") or {}
    result = {}

    printed = (
        compensation.get("compensationTierSummary")
        or compensation.get("summary")
    )
    if printed:
        result["salary"] = printed

    components = compensation.get("summaryComponents") or []
    ranged = [
        component for component in components
        if component.get("minValue") is not None
        or component.get("maxValue") is not None
    ]
    if not ranged:
        return result

    currencies = {
        component.get("currencyCode")
        for component in ranged
        if component.get("currencyCode")
    }
    intervals = {
        component.get("interval")
        for component in ranged
        if component.get("interval")
    }
    if len(currencies) > 1 or len(intervals) > 1:
        return result

    component = ranged[0]
    if component.get("minValue") is not None:
        result["salary_min"] = component["minValue"]
    if component.get("maxValue") is not None:
        result["salary_max"] = component["maxValue"]
    if component.get("currencyCode"):
        result["salary_currency"] = component["currencyCode"]
    if component.get("interval"):
        result["salary_period"] = component["interval"]
    return result


def crawl(ctx):
    response = ctx.fetch(SITE)
    if isinstance(response, str):
        data = json.loads(response)
    else:
        data = response.json()

    for item in data.get("jobs") or []:
        description = item.get("descriptionPlain")
        if description:
            description = description.strip()
        elif item.get("descriptionHtml"):
            description = clean_html(item["descriptionHtml"])

        title = item.get("title")
        url = item.get("jobUrl")
        if not title or not url or not description:
            continue

        locations = []
        if item.get("location"):
            locations.append(item["location"])
        for entry in item.get("secondaryLocations") or []:
            location = (
                entry.get("location")
                if isinstance(entry, dict)
                else entry
            )
            if location and location not in locations:
                locations.append(location)

        fields = {
            "url": url,
            "title": title,
            "company": "Chronosphere",
            "location": item.get("location"),
            "description": description,
            "source": SITE,
            "external_id": item.get("id"),
            "apply_url": item.get("applyUrl"),
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "department": item.get("department") or item.get("team"),
            "posted_at": item.get("publishedAt"),
        }
        if locations:
            fields["locations"] = locations
        fields.update(compensation_fields(item))

        yield Job(**{
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        })
