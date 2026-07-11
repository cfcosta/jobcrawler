
import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/workos?includeCompensation=true"
EXTRA_HOSTS = []


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _locations(job):
    result = []
    primary = _clean(job.get("location"))
    if primary:
        result.append(primary)

    for item in job.get("secondaryLocations") or []:
        loc = None
        if isinstance(item, str):
            loc = item
        elif isinstance(item, dict):
            loc = item.get("location") or item.get("name")
        loc = _clean(loc)
        if loc and loc not in result:
            result.append(loc)

    return result


def _salary_fields(job):
    comp = job.get("compensation") or {}
    fields = {}

    salary_text = _clean(comp.get("compensationTierSummary") or comp.get("scrapeableCompensationSalarySummary"))
    if salary_text:
        fields["salary"] = salary_text

    components = comp.get("summaryComponents") or []
    salary_components = [
        c for c in components
        if isinstance(c, dict)
        and c.get("compensationType") == "Salary"
        and c.get("currencyCode")
        and c.get("minValue") is not None
        and c.get("maxValue") is not None
    ]

    currencies = {c.get("currencyCode") for c in salary_components}
    if len(currencies) == 1 and salary_components:
        c = salary_components[0]
        fields["salary_min"] = c.get("minValue")
        fields["salary_max"] = c.get("maxValue")
        fields["salary_currency"] = c.get("currencyCode")
        period = _clean(c.get("interval"))
        if period:
            fields["salary_period"] = period

    return fields


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        if not job.get("isListed", True):
            continue

        description = _clean(job.get("descriptionPlain") or job.get("descriptionHtml"))
        title = _clean(job.get("title"))
        url = _clean(job.get("jobUrl"))
        location = _clean(job.get("location"))
        if not (title and url and location and description):
            continue

        item = {
            "url": url,
            "title": title,
            "company": "WorkOS",
            "location": location,
            "description": description,
            "source": SITE,
        }

        locs = _locations(job)
        if locs:
            item["locations"] = locs

        mappings = {
            "employment_type": job.get("employmentType"),
            "workplace_type": job.get("workplaceType") or ("Remote" if job.get("isRemote") else None),
            "department": job.get("department") or job.get("team"),
            "posted_at": job.get("publishedAt"),
            "external_id": job.get("id"),
            "apply_url": job.get("applyUrl"),
        }
        for key, value in mappings.items():
            value = _clean(value)
            if value:
                item[key] = value

        item.update(_salary_fields(job))
        yield item
