
import html
import json
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

SITE = "https://boards-api.greenhouse.io/v1/boards/growe/jobs?content=true"
EXTRA_HOSTS = []

def _api_url():
    parts = urlsplit(SITE)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["content"] = "true"
    query["pay_transparency"] = "true"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|tr|section|article)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None

def _field(obj, name):
    if isinstance(obj, dict):
        value = obj.get(name)
        if value not in ("", [], {}):
            return value
    return None

def _salary_fields(job):
    ranges = job.get("pay_input_ranges") or []
    if not ranges:
        return {}
    currencies = {r.get("currency_type") for r in ranges if r.get("currency_type")}
    if len(currencies) != 1:
        return {}
    mins = [r.get("min_cents") for r in ranges if r.get("min_cents") is not None]
    maxs = [r.get("max_cents") for r in ranges if r.get("max_cents") is not None]
    if not mins and not maxs:
        return {}

    out = {"salary_currency": currencies.pop()}
    if mins:
        out["salary_min"] = min(mins) / 100
    if maxs:
        out["salary_max"] = max(maxs) / 100

    periods = [r.get("unit") or r.get("interval") or r.get("period") for r in ranges]
    periods = [p for p in periods if p]
    if len(set(periods)) == 1:
        out["salary_period"] = periods[0]

    texts = [r.get("text") or r.get("label") for r in ranges]
    texts = [t for t in texts if t]
    if texts:
        out["salary"] = "; ".join(texts)

    return out

def crawl(ctx):
    raw = ctx.fetch(_api_url())
    data = json.loads(raw) if isinstance(raw, str) else raw
    jobs = data.get("jobs", []) if isinstance(data, dict) else []

    for job in jobs:
        title = _field(job, "title")
        url = _field(job, "absolute_url")
        description = _clean_html(job.get("content"))
        location_obj = job.get("location") or {}
        location = _field(location_obj, "name") or "Anywhere"

        if not title or not url or not description:
            continue

        offices = job.get("offices") or []
        locations = []
        for office in offices:
            name = _field(office, "name")
            if name:
                locations.append(name)

        departments = job.get("departments") or []
        dept_names = [_field(d, "name") for d in departments]
        dept_names = [d for d in dept_names if d]

        item = {
            "url": url,
            "title": title,
            "company": _field(job, "company_name") or "Growe",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(_field(job, "id") or _field(job, "internal_job_id")),
            "apply_url": url,
        }

        if locations:
            item["locations"] = locations
        if dept_names:
            item["department"] = ", ".join(dept_names)
        if _field(job, "first_published"):
            item["posted_at"] = _field(job, "first_published")
        if _field(job, "application_deadline"):
            item["expires_at"] = _field(job, "application_deadline")

        item.update(_salary_fields(job))
        yield item
