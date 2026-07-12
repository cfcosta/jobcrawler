
import json
SITE = "https://jobs.ashbyhq.com/toggl"
EXTRA_HOSTS = []


def _app_data(text):
    marker = "window.__appData"
    marker_pos = text.index(marker)
    object_pos = text.index("{", marker_pos + len(marker))
    return json.JSONDecoder().raw_decode(text[object_pos:])[0]


def _postal_location(secondary):
    postal = (secondary.get("address") or {}).get("postalAddress") or {}
    parts = []
    for key in ("addressLocality", "addressRegion", "addressCountry"):
        value = postal.get(key)
        if value and value not in parts:
            parts.append(value)
    return ", ".join(parts) or secondary.get("locationName")


def _compensation(posting):
    result = {}
    printed = posting.get("scrapeableCompensationSalarySummary")
    if printed:
        result["salary"] = printed

    components = posting.get("summaryComponents") or []
    salary_components = [
        component for component in components
        if component.get("minValue") is not None
        or component.get("maxValue") is not None
    ]
    currencies = {
        component.get("currencyCode")
        for component in salary_components
        if component.get("currencyCode")
    }
    if salary_components and len(currencies) <= 1:
        component = salary_components[0]
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
    board = _app_data(ctx.fetch(SITE))
    briefs = board.get("jobBoard", {}).get("jobPostings", [])

    for brief in briefs:
        if brief.get("isListed") is False or not brief.get("id"):
            continue

        url = SITE + "/" + brief["id"]
        detail = _app_data(ctx.fetch(url)).get("posting") or {}
        description = detail.get("descriptionPlainText")
        if not description:
            continue

        location = detail.get("locationName") or brief.get("locationName")
        locations = []
        if location:
            locations.append(location)
        for secondary in brief.get("secondaryLocations") or []:
            value = secondary.get("locationName")
            if value:
                locations.append(value)

        fields = {
            "url": url,
            "source": SITE,
            "title": detail.get("title") or brief.get("title"),
            "company": "Toggl",
            "location": location,
            "description": description,
            "external_id": detail.get("jobId") or brief.get("jobId") or brief["id"],
            "employment_type": detail.get("employmentType") or brief.get("employmentType"),
            "workplace_type": detail.get("workplaceType") or brief.get("workplaceType"),
            "department": (
                detail.get("departmentName")
                or brief.get("departmentName")
                or detail.get("teamName")
                or brief.get("teamName")
            ),
            "posted_at": detail.get("publishedDate") or brief.get("publishedDate"),
            "expires_at": detail.get("applicationDeadline") or brief.get("applicationDeadline"),
        }
        if locations:
            fields["locations"] = locations
        fields.update(_compensation(detail))
        yield {key: value for key, value in fields.items() if value is not None}
