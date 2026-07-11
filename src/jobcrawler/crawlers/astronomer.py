
import json
import re
from html import unescape

SITE = "https://api.ashbyhq.com/posting-api/job-board/astronomer?includeCompensation=true"
EXTRA_HOSTS = []


def _response_text(response):
    if isinstance(response, bytes):
        return response.decode("utf-8", "replace")
    return response


def _clean_text(value):
    if not value:
        return None
    value = unescape(str(value))
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = value.strip()
    return value or None


def _add_if_present(job, key, value):
    if value is not None and value != "":
        job[key] = value


def _salary_fields(job, source_job):
    comp = source_job.get("compensation") or {}
    summary = comp.get("scrapeableCompensationSalarySummary") or comp.get("compensationTierSummary")
    _add_if_present(job, "salary", summary)

    components = comp.get("summaryComponents") or []
    structured = []
    for item in components:
        if not isinstance(item, dict):
            continue
        currency = item.get("currencyCode") or item.get("currency")
        period = item.get("interval")
        min_value = item.get("minValue")
        max_value = item.get("maxValue")
        if currency and (min_value is not None or max_value is not None):
            structured.append((min_value, max_value, currency, period))

    currencies = {item[2] for item in structured if item[2]}
    if len(currencies) == 1 and structured:
        mins = [item[0] for item in structured if item[0] is not None]
        maxs = [item[1] for item in structured if item[1] is not None]
        periods = [item[3] for item in structured if item[3]]
        if mins:
            job["salary_min"] = min(mins)
        if maxs:
            job["salary_max"] = max(maxs)
        job["salary_currency"] = next(iter(currencies))
        if periods:
            job["salary_period"] = periods[0]


def crawl(ctx):
    data = json.loads(_response_text(ctx.fetch(SITE)))

    for item in data.get("jobs", []):
        if item.get("isListed") is False:
            continue

        description = _clean_text(item.get("descriptionPlain"))
        if not description:
            continue

        primary_location = item.get("location")
        locations = []
        if primary_location:
            locations.append(primary_location)
        for secondary in item.get("secondaryLocations") or []:
            if isinstance(secondary, dict) and secondary.get("location"):
                locations.append(secondary["location"])

        job = {
            "url": item.get("jobUrl") or SITE,
            "title": item.get("title"),
            "company": "Astronomer",
            "location": primary_location or (locations[0] if locations else "Remote"),
            "description": description,
            "source": SITE,
        }

        if locations:
            job["locations"] = locations

        _add_if_present(job, "external_id", item.get("id"))
        _add_if_present(job, "apply_url", item.get("applyUrl"))
        _add_if_present(job, "department", item.get("department") or item.get("team"))
        _add_if_present(job, "employment_type", item.get("employmentType"))
        _add_if_present(job, "workplace_type", item.get("workplaceType") or ("Remote" if item.get("isRemote") else None))
        _add_if_present(job, "posted_at", item.get("publishedAt"))

        _salary_fields(job, item)

        yield job
