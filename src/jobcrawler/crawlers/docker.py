
import html
import json
import re

SITE = 'https://api.ashbyhq.com/posting-api/job-board/docker?includeCompensation=true'
EXTRA_HOSTS = []


def _clean_html(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</p\s*>", "\n\n", value)
    value = re.sub(r"(?i)</li\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\r", "\n", value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n\s*\n\s*\n+", "\n\n", value)
    return value.strip() or None


def _location(job):
    locations = []
    primary = job.get("location")
    if primary:
        locations.append(primary)
    for item in job.get("secondaryLocations") or []:
        loc = item.get("location")
        if loc and loc not in locations:
            locations.append(loc)
    return "; ".join(locations) or None


def _salary_fields(job):
    comp = job.get("compensation") or {}
    components = comp.get("summaryComponents") or []
    for component in components:
        if component.get("compensationType") == "Salary":
            min_value = component.get("minValue")
            max_value = component.get("maxValue")
            currency = component.get("currencyCode")
            if min_value is None and max_value is None and not currency:
                continue
            fields = {}
            if min_value is not None:
                fields["salary_min"] = min_value
            if max_value is not None:
                fields["salary_max"] = max_value
            if currency:
                fields["salary_currency"] = currency
            if component.get("interval"):
                fields["salary_period"] = component.get("interval")
            return fields
    return {}


def _add_if_present(record, key, value):
    if value is not None and value != "":
        record[key] = value


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue

        description = job.get("descriptionPlain") or _clean_html(job.get("descriptionHtml"))
        url = job.get("jobUrl") or (SITE.rstrip("/") + "/" + job.get("id", ""))
        record = {
            "url": url,
            "title": job.get("title"),
            "company": "Docker",
            "location": _location(job),
            "description": description,
            "source": SITE,
        }

        _add_if_present(record, "external_id", job.get("id"))
        _add_if_present(record, "apply_url", job.get("applyUrl"))
        _add_if_present(record, "employment_type", job.get("employmentType"))
        _add_if_present(record, "workplace_type", job.get("workplaceType") or ("Remote" if job.get("isRemote") else None))
        _add_if_present(record, "department", job.get("department"))
        _add_if_present(record, "posted_at", job.get("publishedAt"))

        record.update(_salary_fields(job))

        if record.get("title") and record.get("location") and record.get("description"):
            yield record
