
import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/notion?includeCompensation=true'
EXTRA_HOSTS = []


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _put(job, key, value):
    value = _clean(value)
    if value is not None:
        job[key] = value


def _salary_from_ashby(compensation):
    components = (compensation or {}).get("summaryComponents") or []
    for comp in components:
        min_value = comp.get("minValue")
        max_value = comp.get("maxValue")
        currency = _clean(comp.get("currencyCode"))
        interval = _clean(comp.get("interval"))
        if min_value is None and max_value is None:
            continue
        salary = {}
        if min_value is not None:
            salary["min"] = min_value
        if max_value is not None:
            salary["max"] = max_value
        if currency:
            salary["currency"] = currency
        if interval:
            salary["interval"] = interval
        return salary
    return None


def crawl(ctx):
    response = ctx.fetch(SITE)
    data = response.json() if hasattr(response, "json") else json.loads(response.text if hasattr(response, "text") else response)

    for item in data.get("jobs", []):
        if item.get("isListed") is False:
            continue

        description = _clean(item.get("descriptionPlain"))
        title = _clean(item.get("title"))
        url = _clean(item.get("jobUrl"))
        location = _clean(item.get("location"))

        if not (description and title and url and location):
            continue

        job = {
            "url": url,
            "title": title,
            "company": "Notion",
            "location": location,
            "description": description,
            "source": SITE,
        }

        _put(job, "employment_type", item.get("employmentType"))
        _put(job, "workplace_type", item.get("workplaceType"))
        _put(job, "department", item.get("departmentName") or item.get("department") or item.get("team"))
        _put(job, "posted_at", item.get("publishedAt"))
        _put(job, "external_id", item.get("id"))
        _put(job, "apply_url", item.get("applyUrl"))

        salary = _salary_from_ashby(item.get("compensation"))
        if salary:
            job["salary"] = salary

        yield job
