
import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/Grindr%20LLC?includeCompensation=true'
EXTRA_HOSTS = []


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _locations(job):
    seen = set()
    values = []

    primary = _clean(job.get("location"))
    if primary:
        values.append(primary)
        seen.add(primary)

    for item in job.get("secondaryLocations") or []:
        if isinstance(item, dict):
            loc = _clean(item.get("location"))
        else:
            loc = _clean(item)
        if loc and loc not in seen:
            values.append(loc)
            seen.add(loc)

    return values


def _salary_fields(job):
    comp = job.get("compensation") or {}
    out = {}

    salary_text = _clean(
        comp.get("compensationTierSummary")
        or comp.get("scrapeableCompensationSalarySummary")
    )
    if salary_text:
        out["salary"] = salary_text

    components = comp.get("summaryComponents") or []
    if len(components) == 1 and isinstance(components[0], dict):
        component = components[0]
        min_value = component.get("minValue")
        max_value = component.get("maxValue")
        currency = _clean(component.get("currencyCode"))
        interval = _clean(component.get("interval"))

        if min_value is not None:
            out["salary_min"] = min_value
        if max_value is not None:
            out["salary_max"] = max_value
        if currency:
            out["salary_currency"] = currency
        if interval:
            out["salary_period"] = interval

    return out


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs") or []:
        if job.get("isListed") is False:
            continue

        title = _clean(job.get("title"))
        description = _clean(job.get("descriptionPlain") or job.get("descriptionHtml"))
        url = _clean(job.get("jobUrl"))
        location = _clean(job.get("location"))
        if not (title and description and url and location):
            continue

        item = {
            "url": url,
            "title": title,
            "company": "Grindr",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": _clean(job.get("id")),
        }

        locs = _locations(job)
        if locs:
            item["locations"] = locs

        department = _clean(job.get("department") or job.get("team"))
        if department:
            item["department"] = department

        employment_type = _clean(job.get("employmentType"))
        if employment_type:
            item["employment_type"] = employment_type

        workplace_type = _clean(job.get("workplaceType"))
        if not workplace_type and job.get("isRemote") is True:
            workplace_type = "Remote"
        if workplace_type:
            item["workplace_type"] = workplace_type

        posted_at = _clean(job.get("publishedAt"))
        if posted_at:
            item["posted_at"] = posted_at

        apply_url = _clean(job.get("applyUrl"))
        if apply_url:
            item["apply_url"] = apply_url

        item.update(_salary_fields(job))

        # Drop unset optional fields rather than yielding empty strings.
        yield {k: v for k, v in item.items() if v is not None}
