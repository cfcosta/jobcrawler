
import json
import re
from html import unescape

SITE = 'https://api.ashbyhq.com/posting-api/job-board/confluent?includeCompensation=true'
EXTRA_HOSTS = []

def _response_text(resp):
    if isinstance(resp, str):
        return resp
    if hasattr(resp, "text"):
        return resp.text
    return str(resp)

def _clean_html(value):
    if not value:
        return None
    text = re.sub(r"<[^>]+>", " ", unescape(value))
    text = re.sub(r"\s+", " ", text).strip()
    return text or None

def _salary_fields(job):
    comp = job.get("compensation") or {}
    fields = {}
    salary_text = comp.get("scrapeableCompensationSalarySummary") or comp.get("compensationTierSummary")
    if salary_text:
        fields["salary"] = salary_text

    salary_components = [
        c for c in (comp.get("summaryComponents") or [])
        if c.get("compensationType") == "Salary"
    ]
    currencies = {c.get("currencyCode") for c in salary_components if c.get("currencyCode")}
    if len(salary_components) == 1 and len(currencies) == 1:
        c = salary_components[0]
        if c.get("minValue") is not None:
            fields["salary_min"] = c.get("minValue")
        if c.get("maxValue") is not None:
            fields["salary_max"] = c.get("maxValue")
        fields["salary_currency"] = c.get("currencyCode")
        if c.get("interval"):
            fields["salary_period"] = c.get("interval")
    return fields

def _job_record(job):
    primary_location = job.get("location")
    locations = []
    if primary_location:
        locations.append(primary_location)
    for item in job.get("secondaryLocations") or []:
        loc = item.get("location") if isinstance(item, dict) else item
        if loc:
            locations.append(loc)

    description = job.get("descriptionPlain") or _clean_html(job.get("descriptionHtml"))
    record = {
        "url": job.get("jobUrl"),
        "title": job.get("title"),
        "company": "Confluent",
        "location": primary_location,
        "description": description,
        "source": SITE,
        "external_id": job.get("id"),
        "apply_url": job.get("applyUrl"),
        "department": job.get("department") or job.get("team"),
        "employment_type": job.get("employmentType"),
        "posted_at": job.get("publishedAt"),
    }
    if len(locations) > 1:
        record["locations"] = locations
    workplace = job.get("workplaceType")
    if workplace is None and job.get("isRemote") is True:
        workplace = "Remote"
    if workplace:
        record["workplace_type"] = workplace

    record.update(_salary_fields(job))
    return {k: v for k, v in record.items() if v not in (None, "")}

def crawl(ctx):
    data = json.loads(_response_text(ctx.fetch(SITE)))
    for job in data.get("jobs") or []:
        if job.get("isListed") is False:
            continue
        record = _job_record(job)
        if record.get("url") and record.get("title") and record.get("location") and record.get("description"):
            yield record
