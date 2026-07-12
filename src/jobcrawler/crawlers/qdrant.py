import json
import re
from html import unescape

SITE = "https://api.ashbyhq.com/posting-api/job-board/qdrant.tech?includeCompensation=true"
EXTRA_HOSTS = []


def _plain_text(value):
    if not value:
        return None
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(?:p|div|li|h[1-6])>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = unescape(value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _salary_fields(job):
    compensation = job.get("compensation") or {}
    components = compensation.get("summaryComponents") or []

    for component in components:
        minimum = component.get("minValue")
        maximum = component.get("maxValue")
        currency = component.get("currencyCode")
        period = component.get("interval")
        if minimum is None and maximum is None:
            continue

        result = {}
        if minimum is not None:
            result["salary_min"] = minimum
        if maximum is not None:
            result["salary_max"] = maximum
        if currency:
            result["salary_currency"] = currency
        if period:
            result["salary_period"] = period

        printed = (
            component.get("summary")
            or component.get("displayValue")
            or compensation.get("compensationTierSummary")
            or compensation.get("scrapeableCompensationSalarySummary")
        )
        if printed:
            result["salary"] = printed
        return result

    return {}


def crawl(ctx):
    body = ctx.fetch(SITE)
    data = json.loads(body)

    for posting in data.get("jobs") or []:
        if posting.get("isListed") is False:
            continue

        description = posting.get("descriptionPlain")
        if not description:
            description = _plain_text(posting.get("descriptionHtml"))
        if not description:
            continue

        primary_location = posting.get("location")
        if not primary_location:
            continue

        locations = [primary_location]
        for secondary in posting.get("secondaryLocations") or []:
            location = secondary.get("location")
            if location and location not in locations:
                locations.append(location)

        fields = {
            "url": posting.get("jobUrl"),
            "source": SITE,
            "title": posting.get("title"),
            "company": "Qdrant",
            "location": primary_location,
            "locations": locations,
            "description": description,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
        }
        fields.update(_salary_fields(posting))

        yield {key: value for key, value in fields.items() if value is not None}
