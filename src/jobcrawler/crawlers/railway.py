
import json
import re
import html

SITE = "https://api.ashbyhq.com/posting-api/job-board/railway?includeCompensation=true"
EXTRA_HOSTS = []


def _text_response(resp):
    if isinstance(resp, str):
        return resp
    if hasattr(resp, "text"):
        return resp.text
    return str(resp)


def _clean_html(value):
    value = html.unescape(value or "")
    value = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(p|div|li|h[1-6]|section|tr)>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"[ \t\r\f\v]+", " ", value).strip()


def _first_present(*values):
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _job_record(job):
    description = _first_present(job.get("descriptionPlain"), _clean_html(job.get("descriptionHtml")))
    location = job.get("location")
    if isinstance(location, dict):
        location = _first_present(location.get("name"), location.get("displayName"))

    workplace_type = _first_present(job.get("workplaceType"), "Remote" if job.get("isRemote") is True else None)

    record = {
        "url": job.get("jobUrl") or job.get("applyUrl"),
        "title": job.get("title"),
        "company": "Railway",
        "location": location,
        "description": description,
        "source": SITE,
        "external_id": job.get("id"),
        "apply_url": job.get("applyUrl"),
        "employment_type": job.get("employmentType"),
        "workplace_type": workplace_type,
        "department": _first_present(job.get("department"), job.get("team")),
        "posted_at": job.get("publishedAt"),
    }

    comp = job.get("compensation") or {}
    components = comp.get("summaryComponents") or []
    if components:
        for component in components:
            min_value = component.get("minValue")
            max_value = component.get("maxValue")
            currency = component.get("currencyCode")
            interval = component.get("interval")
            if min_value is not None:
                record["salary_min"] = min_value
            if max_value is not None:
                record["salary_max"] = max_value
            if currency:
                record["salary_currency"] = currency
            if interval:
                record["salary_period"] = interval
            if min_value is not None or max_value is not None or currency or interval:
                break

    return {k: v for k, v in record.items() if v is not None and v != ""}


def crawl(ctx):
    data = json.loads(_text_response(ctx.fetch(SITE)))
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue
        record = _job_record(job)
        if record.get("url") and record.get("title") and record.get("description"):
            yield record
