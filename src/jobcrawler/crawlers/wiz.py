
import json
import re
import html

SITE = "https://boards-api.greenhouse.io/v1/boards/wizinc/jobs?content=true"
EXTRA_HOSTS = []

TAG_BREAK_RE = re.compile(r"(?i)<\s*/?\s*(p|div|br|li|ul|ol|h[1-6]|tr|table|section|article)\b[^>]*>")
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"[ \t\r\f\v]+")
NL_RE = re.compile(r"\n\s*\n+")

def _text_from_greenhouse_content(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", text)
    text = TAG_BREAK_RE.sub("\n", text)
    text = TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = WS_RE.sub(" ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = NL_RE.sub("\n\n", text)
    text = text.strip()
    return text or None

def _first_nonempty(*values):
    for value in values:
        if value is not None and value != "":
            return value
    return None

def _metadata_value(job, name):
    for item in job.get("metadata") or []:
        if (item.get("name") or "").strip().lower() == name.lower():
            value = item.get("value")
            if value not in (None, ""):
                return value
    return None

def _pay_fields(job):
    ranges = job.get("pay_input_ranges") or []
    if not ranges:
        return {}
    currencies = {r.get("currency_type") for r in ranges if r.get("currency_type")}
    if len(currencies) != 1:
        return {}
    mins = [r.get("min_cents") for r in ranges if isinstance(r.get("min_cents"), (int, float))]
    maxs = [r.get("max_cents") for r in ranges if isinstance(r.get("max_cents"), (int, float))]
    fields = {"salary_currency": next(iter(currencies))}
    if mins:
        fields["salary_min"] = min(mins) / 100
    if maxs:
        fields["salary_max"] = max(maxs) / 100
    # Greenhouse pay_input_ranges may include period/interval wording on some boards.
    for key in ("pay_period", "period", "interval"):
        for item in ranges:
            if item.get(key):
                fields["salary_period"] = item.get(key)
                break
        if fields.get("salary_period"):
            break
    return fields

def crawl(ctx):
    url = SITE + "&pay_transparency=true"
    data = json.loads(ctx.fetch(url))
    for job in data.get("jobs") or []:
        title = _first_nonempty(job.get("title"))
        description = _text_from_greenhouse_content(job.get("content"))
        loc_obj = job.get("location") or {}
        location = _first_nonempty(loc_obj.get("name"))
        job_url = _first_nonempty(job.get("absolute_url"))
        if not (title and description and location and job_url):
            continue

        offices = []
        for office in job.get("offices") or []:
            name = office.get("name")
            if name and name not in offices:
                offices.append(name)

        departments = []
        for dept in job.get("departments") or []:
            name = dept.get("name")
            if name and name not in departments:
                departments.append(name)

        expires_at = _first_nonempty(job.get("application_deadline"), _metadata_value(job, "Application Close Date"))

        item = {
            "url": job_url,
            "title": title,
            "company": "Wiz",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(_first_nonempty(job.get("id"), job.get("internal_job_id"), job.get("requisition_id"))),
            "apply_url": job_url,
        }
        if offices:
            item["locations"] = offices
        if departments:
            item["department"] = ", ".join(departments)
        if job.get("first_published"):
            item["posted_at"] = job.get("first_published")
        if expires_at:
            item["expires_at"] = expires_at

        item.update(_pay_fields(job))
        yield item
