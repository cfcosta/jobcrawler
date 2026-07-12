import html
import json
import re

SITE = "https://api.ashbyhq.com/posting-api/job-board/sift?includeCompensation=true"
EXTRA_HOSTS = []

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"[ \t\r\f\v]+")


def _html_to_text(value):
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(?:p|div|li|h[1-6])\s*>", "\n", value)
    value = _TAG_RE.sub("", value)
    lines = (_SPACE_RE.sub(" ", line).strip() for line in html.unescape(value).splitlines())
    return "\n".join(line for line in lines if line)


def _salary_fields(job):
    compensation = job.get("compensation") or {}
    salary_components = [
        component
        for component in (compensation.get("summaryComponents") or [])
        if component.get("compensationType") == "Salary"
        and component.get("currencyCode")
        and (component.get("minValue") is not None or component.get("maxValue") is not None)
    ]
    currencies = {component["currencyCode"] for component in salary_components}
    if not salary_components or len(currencies) != 1:
        return {}

    component = salary_components[0]
    fields = {
        "salary_currency": component["currencyCode"],
    }
    if component.get("minValue") is not None:
        fields["salary_min"] = component["minValue"]
    if component.get("maxValue") is not None:
        fields["salary_max"] = component["maxValue"]
    if component.get("interval"):
        fields["salary_period"] = component["interval"]

    printed = (
        compensation.get("scrapeableCompensationSalarySummary")
        or compensation.get("compensationTierSummary")
    )
    if printed:
        fields["salary"] = printed
    return fields


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for item in data.get("jobs", []):
        if item.get("isListed") is not True:
            continue

        description = item.get("descriptionPlain")
        if not description and item.get("descriptionHtml"):
            description = _html_to_text(item["descriptionHtml"])

        location = item.get("location")
        locations = []
        if location:
            locations.append(location)
        for secondary in item.get("secondaryLocations") or []:
            secondary_location = secondary.get("location")
            if secondary_location and secondary_location not in locations:
                locations.append(secondary_location)

        fields = {
            "url": item.get("jobUrl"),
            "title": item.get("title"),
            "company": "Sift",
            "location": location,
            "description": description,
            "source": SITE,
        }
        optional = {
            "locations": locations or None,
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "department": item.get("department"),
            "posted_at": item.get("publishedAt"),
            "external_id": item.get("id"),
            "apply_url": item.get("applyUrl"),
        }
        fields.update({key: value for key, value in optional.items() if value not in (None, "")})
        fields.update(_salary_fields(item))
        yield fields
