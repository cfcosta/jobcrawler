from __future__ import annotations

import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/fal-ai?includeCompensation=true"
EXTRA_HOSTS = []


def _locations(job):
    values = []
    primary = job.get("location")
    if primary:
        values.append(primary)
    for item in job.get("secondaryLocations") or []:
        value = item.get("location") if isinstance(item, dict) else item
        if value and value not in values:
            values.append(value)
    return values or None


def _salary_fields(job):
    compensation = job.get("compensation") or {}
    salary_components = [
        component
        for component in compensation.get("summaryComponents") or []
        if component.get("compensationType") == "Salary"
    ]
    if not salary_components:
        return {}

    currencies = {c.get("currencyCode") for c in salary_components if c.get("currencyCode")}
    periods = {c.get("interval") for c in salary_components if c.get("interval")}
    if len(currencies) > 1 or len(periods) > 1:
        return {}

    mins = [c["minValue"] for c in salary_components if c.get("minValue") is not None]
    maxes = [c["maxValue"] for c in salary_components if c.get("maxValue") is not None]
    result = {}
    if mins:
        result["salary_min"] = min(mins)
    if maxes:
        result["salary_max"] = max(maxes)
    if currencies:
        result["salary_currency"] = next(iter(currencies))
    if periods:
        result["salary_period"] = next(iter(periods))

    printed = compensation.get("compensationTierSummary")
    if printed:
        result["salary"] = printed
    return result


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))
    for posting in payload.get("jobs") or []:
        if posting.get("isListed") is not True:
            continue

        workplace_type = posting.get("workplaceType")
        if not workplace_type and posting.get("isRemote") is True:
            workplace_type = "Remote"

        fields = {
            "url": posting.get("jobUrl"),
            "title": posting.get("title"),
            "company": "fal-ai",
            "location": posting.get("location"),
            "locations": _locations(posting),
            "description": posting.get("descriptionPlain"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": workplace_type,
            "department": posting.get("department") or posting.get("team"),
            "posted_at": posting.get("publishedAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "source": SITE,
        }
        fields.update(_salary_fields(posting))
        yield {key: value for key, value in fields.items() if value is not None}
