
import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/apollo-graphql?includeCompensation=true'
EXTRA_HOSTS = []


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _locations(job):
    primary = _clean(job.get("location"))
    values = []
    if primary:
        values.append(primary)
    for item in job.get("secondaryLocations") or []:
        if isinstance(item, dict):
            loc = _clean(item.get("location"))
        else:
            loc = _clean(item)
        if loc and loc not in values:
            values.append(loc)
    return values


def _salary_fields(job):
    comp = job.get("compensation") or {}
    out = {}
    printed = _clean(comp.get("compensationTierSummary") or comp.get("scrapeableCompensationSalarySummary"))
    if printed:
        out["salary"] = printed

    salary_components = [
        c for c in (comp.get("summaryComponents") or [])
        if isinstance(c, dict) and c.get("compensationType") == "Salary"
    ]
    currencies = {c.get("currencyCode") for c in salary_components if c.get("currencyCode")}
    if len(currencies) == 1 and salary_components:
        mins = [c.get("minValue") for c in salary_components if c.get("minValue") is not None]
        maxs = [c.get("maxValue") for c in salary_components if c.get("maxValue") is not None]
        intervals = [c.get("interval") for c in salary_components if c.get("interval")]
        if mins:
            out["salary_min"] = min(mins)
        if maxs:
            out["salary_max"] = max(maxs)
        out["salary_currency"] = next(iter(currencies))
        if intervals:
            out["salary_period"] = intervals[0]
    return out


def crawl(ctx):
    raw = ctx.fetch(SITE)
    if hasattr(raw, "text"):
        raw = raw.text
    data = json.loads(raw)
    for job in data.get("jobs", []):
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
            "company": "Apollo GraphQL",
            "location": location,
            "description": description,
            "source": SITE,
        }

        locs = _locations(job)
        if locs:
            item["locations"] = locs

        mapping = [
            ("employmentType", "employment_type"),
            ("workplaceType", "workplace_type"),
            ("department", "department"),
            ("publishedAt", "posted_at"),
            ("id", "external_id"),
            ("applyUrl", "apply_url"),
        ]
        for src, dest in mapping:
            val = _clean(job.get(src))
            if val is not None:
                item[dest] = val

        item.update(_salary_fields(job))
        yield item
