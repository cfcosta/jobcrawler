
import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/plaid?includeCompensation=true"


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = " ".join(value.split())
        return value or None
    return value


def _locations(job):
    names = []
    primary = _clean(job.get("location"))
    if primary:
        names.append(primary)
    for item in job.get("secondaryLocations") or []:
        loc = _clean(item.get("location") if isinstance(item, dict) else None)
        if loc and loc not in names:
            names.append(loc)
    return ", ".join(names) if names else None


def _salary(job):
    comp = job.get("compensation") or {}
    summary = _clean(comp.get("scrapeableCompensationSalarySummary"))
    if summary:
        return summary
    for component in comp.get("summaryComponents") or []:
        if component.get("compensationType") != "Salary":
            continue
        text = _clean(component.get("summary"))
        if text:
            return text
        min_value = component.get("minValue")
        max_value = component.get("maxValue")
        currency = _clean(component.get("currencyCode"))
        if min_value is not None and max_value is not None and currency:
            return "%s %s - %s" % (currency, min_value, max_value)
    return _clean(comp.get("compensationTierSummary"))


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs") or []:
        if job.get("isListed") is False:
            continue

        description = _clean(job.get("descriptionPlain"))
        title = _clean(job.get("title"))
        url = _clean(job.get("jobUrl"))
        location = _locations(job)
        if not (description and title and url and location):
            continue

        item = {
            "url": url,
            "title": title,
            "company": "Plaid",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": _clean(job.get("id")),
            "apply_url": _clean(job.get("applyUrl")),
            "employment_type": _clean(job.get("employmentType")),
            "workplace_type": _clean(job.get("workplaceType")),
            "department": _clean(job.get("team") or job.get("department")),
            "posted_at": _clean(job.get("publishedAt")),
            "salary": _salary(job),
        }
        yield {key: value for key, value in item.items() if value is not None}
