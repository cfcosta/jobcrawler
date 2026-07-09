
import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/tigerdata?includeCompensation=true"
EXTRA_HOSTS = []


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue

        title = _clean(job.get("title"))
        description = _clean(job.get("descriptionPlain"))
        url = _clean(job.get("jobUrl"))
        location = _clean(job.get("location"))
        if not (title and description and url and location):
            continue

        out = {
            "url": url,
            "title": title,
            "company": "Tiger Data",
            "location": location,
            "description": description,
            "source": SITE,
        }

        mappings = {
            "employmentType": "employment_type",
            "workplaceType": "workplace_type",
            "department": "department",
            "publishedAt": "posted_at",
            "id": "external_id",
            "applyUrl": "apply_url",
        }
        for src, dest in mappings.items():
            value = _clean(job.get(src))
            if value is not None:
                out[dest] = value

        comp = job.get("compensation") or {}
        components = comp.get("summaryComponents") or []
        salary_parts = []
        for component in components:
            if not isinstance(component, dict):
                continue
            min_value = component.get("minValue")
            max_value = component.get("maxValue")
            currency = _clean(component.get("currencyCode"))
            interval = _clean(component.get("interval"))
            if min_value is None and max_value is None:
                continue
            salary_part = {
                "min_value": min_value,
                "max_value": max_value,
            }
            if currency is not None:
                salary_part["currency"] = currency
            if interval is not None:
                salary_part["interval"] = interval
            salary_parts.append(salary_part)
        if salary_parts:
            out["salary"] = salary_parts

        yield out
