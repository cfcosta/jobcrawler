import html
import json
import re

SITE = "https://api.ashbyhq.com/posting-api/job-board/decagon?includeCompensation=true"
EXTRA_HOSTS = []


def _html_to_text(value):
    if not value:
        return None

    value = re.sub(
        r"<\s*(br|/p|/div|/li|/h[1-6])\s*/?\s*>",
        "\n",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    lines = [line.strip() for line in value.splitlines()]

    result = []
    for line in lines:
        if line or (result and result[-1]):
            result.append(line)

    text = "\n".join(result).strip()
    return text or None


def _salary_fields(posting):
    compensation = posting.get("compensation") or {}
    components = [
        component
        for component in compensation.get("summaryComponents") or []
        if component.get("compensationType") == "Salary"
        and (
            component.get("minValue") is not None
            or component.get("maxValue") is not None
        )
    ]
    currencies = {
        component.get("currencyCode")
        for component in components
        if component.get("currencyCode")
    }

    # One structured quartet cannot accurately represent mixed currencies.
    if not components or len(currencies) > 1:
        return {}

    component = components[0]
    result = {}

    printed = compensation.get("scrapeableCompensationSalarySummary")
    if printed:
        result["salary"] = printed
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
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs") or []:
        if posting.get("isListed") is False:
            continue

        primary_location = posting.get("location")
        locations = []
        if primary_location:
            locations.append(primary_location)

        for secondary in posting.get("secondaryLocations") or []:
            secondary_location = secondary.get("location")
            if secondary_location:
                locations.append(secondary_location)

        description = posting.get("descriptionPlain")
        if not description:
            description = _html_to_text(posting.get("descriptionHtml"))

        job = {
            "url": posting.get("jobUrl"),
            "title": posting.get("title"),
            "company": "Decagon",
            "location": primary_location,
            "description": description,
            "source": SITE,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
        }

        if locations:
            job["locations"] = locations

        job.update(_salary_fields(posting))
        yield {
            key: value
            for key, value in job.items()
            if value is not None and value != ""
        }
