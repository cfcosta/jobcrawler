
import json
import re
import html

SITE = 'https://boards-api.greenhouse.io/v1/boards/morganmorganjobsapplynow/departments/4071963004'
EXTRA_HOSTS = []

API_URL = "https://boards-api.greenhouse.io/v1/boards/morganmorganjobsapplynow/jobs?content=true&pay_transparency=true"
COMPANY = "Morgan & Morgan, P.A."

def _clean_description(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr|section|article)\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*(p|div|li|h[1-6]|tr|section|article)(\s[^>]*)?>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip() or None

def _names(items):
    out = []
    for item in items or []:
        if isinstance(item, dict):
            name = item.get("name")
            if name and name not in out:
                out.append(name)
    return out

def _salary_fields(job):
    ranges = job.get("pay_input_ranges") or []
    currencies = set()
    for item in ranges:
        if item.get("currency_type"):
            currencies.add(item.get("currency_type"))
    if len(currencies) != 1:
        return {}

    mins = []
    maxs = []
    for item in ranges:
        min_cents = item.get("min_cents")
        max_cents = item.get("max_cents")
        if isinstance(min_cents, (int, float)):
            mins.append(min_cents)
        if isinstance(max_cents, (int, float)):
            maxs.append(max_cents)
    if not mins and not maxs:
        return {}

    fields = {"salary_currency": list(currencies)[0]}
    if mins:
        fields["salary_min"] = min(mins) / 100
    if maxs:
        fields["salary_max"] = max(maxs) / 100
    return fields

def crawl(ctx):
    data = json.loads(ctx.fetch(API_URL))
    for raw in data.get("jobs", []):
        title = raw.get("title")
        url = raw.get("absolute_url")
        loc = raw.get("location") or {}
        location = loc.get("name") if isinstance(loc, dict) else None
        description = _clean_description(raw.get("content"))

        if not (title and url and location and description):
            continue

        job = {
            "url": url,
            "title": title,
            "company": raw.get("company_name") or COMPANY,
            "location": location,
            "description": description,
            "source": SITE,
        }

        office_names = _names(raw.get("offices"))
        if office_names:
            job["locations"] = office_names

        department_names = _names(raw.get("departments"))
        if department_names:
            job["department"] = department_names[0]

        if raw.get("first_published"):
            job["posted_at"] = raw.get("first_published")
        if raw.get("application_deadline"):
            job["expires_at"] = raw.get("application_deadline")
        if raw.get("id") is not None:
            job["external_id"] = str(raw.get("id"))

        job.update(_salary_fields(raw))
        yield job
