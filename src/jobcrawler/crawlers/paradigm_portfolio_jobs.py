
import json
import re
from html import unescape

SITE = "https://jobs.paradigm.xyz/"
EXTRA_HOSTS = []

HOSTED_JOBS = [
    ("d7023bf9-dadf-4131-8bff-cd6ba3e602a8", "Rift"),
    ("17de9bfc-75cb-4c6d-ab08-90ce08fb9942", "Rift"),
    ("5cc7f852-9941-4c81-9cba-a1c9365d7ee7", "Rift"),
]

def _join(path):
    return SITE.rstrip("/") + "/" + path.lstrip("/")

def _clean(value):
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = unescape(value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = value.replace("\r", "\n")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = value.strip()
    return value or None

def _labels(items):
    vals = []
    for item in items or []:
        if isinstance(item, dict):
            val = item.get("label") or item.get("value") or item.get("id")
        else:
            val = item
        if val:
            vals.append(str(val))
    return vals

def _num_text(value):
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value == int(value):
            return str(int(value))
        return str(value)
    return str(value)

def _salary_text(src):
    if not isinstance(src, dict):
        return None
    min_value = src.get("minValue")
    max_value = src.get("maxValue")
    if min_value is None and max_value is None:
        return None
    currency = src.get("currency")
    if isinstance(currency, dict):
        currency = currency.get("value") or currency.get("label")
    period = src.get("period")
    if isinstance(period, dict):
        period = period.get("value") or period.get("label")

    if min_value is not None and max_value is not None:
        text = _num_text(min_value) + " - " + _num_text(max_value)
    elif min_value is not None:
        text = "from " + _num_text(min_value)
    else:
        text = "up to " + _num_text(max_value)
    if currency:
        text += " " + str(currency)
    if period:
        text += " per " + str(period)
    return text

def crawl(ctx):
    for job_id, company_id in HOSTED_JOBS:
        detail_url = _join("/api-boards/jobs/%s?companyId=%s" % (job_id, company_id))
        data = json.loads(ctx.fetch(detail_url))
        job = data.get("job") or {}
        description = _clean(job.get("description"))
        title = _clean(job.get("title"))
        company = _clean(job.get("companyName") or company_id)
        locations = _labels(job.get("locations"))
        location = _clean(", ".join(locations)) or "Remote"

        if not (description and title and company):
            continue

        item = {
            "url": _join("/jobs/rift/%s" % job_id),
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": job_id,
        }

        job_types = _labels(job.get("jobTypes"))
        if job_types:
            item["employment_type"] = ", ".join(job_types)

        remote_pref = job.get("remotePreference")
        if remote_pref:
            item["workplace_type"] = str(remote_pref)

        posted = job.get("postedDate") or job.get("posted") or job.get("created")
        if posted:
            item["posted_at"] = posted

        expires = job.get("expiryDate")
        if expires:
            item["expires_at"] = expires

        salary = _salary_text(job.get("salaryRange"))
        if salary:
            item["salary"] = salary

        yield item
