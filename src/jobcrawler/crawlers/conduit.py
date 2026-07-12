import json
import re
from html import unescape

SITE = 'https://api.ashbyhq.com/posting-api/job-board/Conduit?includeCompensation=true'
EXTRA_HOSTS = []


def _text_from_html(value):
    if not value:
        return None
    value = re.sub(r"(?i)<br\s*/?>|</(?:p|div|li|h[1-6])>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = unescape(value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n\s*\n+", "\n\n", value)
    return value.strip() or None


def _compensation(job):
    compensation = job.get("compensation")
    if not isinstance(compensation, dict):
        return {}

    result = {}
    for key in ("scrapeableCompensationSalarySummary", "compensationTierSummary"):
        value = compensation.get(key)
        if isinstance(value, str) and value.strip():
            result["salary"] = value
            break

    components = compensation.get("summaryComponents")
    if isinstance(components, list):
        for component in components:
            if not isinstance(component, dict):
                continue
            minimum = component.get("minValue")
            maximum = component.get("maxValue")
            currency = component.get("currencyCode")
            interval = component.get("interval")
            if (
                minimum is not None
                and maximum is not None
                and currency
                and interval
            ):
                result.update(
                    salary_min=minimum,
                    salary_max=maximum,
                    salary_currency=currency,
                    salary_period=interval,
                )
                break
    return result


def crawl(ctx):
    response = ctx.fetch(SITE)
    payload = json.loads(response)
    for job in payload.get("jobs", []):
        if not isinstance(job, dict) or job.get("isListed") is False:
            continue

        description = job.get("descriptionPlain")
        if not isinstance(description, str) or not description.strip():
            description = _text_from_html(job.get("descriptionHtml"))
        else:
            description = description.strip()

        location = job.get("location")
        locations = []
        if isinstance(location, str) and location.strip():
            locations.append(location)
        for secondary in job.get("secondaryLocations") or []:
            if not isinstance(secondary, dict):
                continue
            value = secondary.get("location")
            if isinstance(value, str) and value.strip() and value not in locations:
                locations.append(value)

        workplace_type = job.get("workplaceType")
        if not workplace_type and job.get("isRemote") is True:
            workplace_type = "Remote"

        fields = {
            "url": job.get("jobUrl"),
            "source": SITE,
            "title": job.get("title"),
            "company": "Conduit",
            "location": location,
            "description": description,
            "external_id": job.get("id"),
            "apply_url": job.get("applyUrl"),
            "department": job.get("department"),
            "employment_type": job.get("employmentType"),
            "workplace_type": workplace_type,
            "posted_at": job.get("publishedAt"),
        }
        if locations:
            fields["locations"] = locations
        fields.update(_compensation(job))
        yield {key: value for key, value in fields.items() if value is not None and value != ""}
