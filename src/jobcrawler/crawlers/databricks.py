
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/databricks/jobs?content=true"
EXTRA_HOSTS = []


def _clean_text(value):
    if not value:
        return None
    text = value
    # Greenhouse content is entity-escaped HTML; some boards double-escape it.
    for _ in range(3):
        unescaped = html.unescape(text)
        if unescaped == text:
            break
        text = unescaped
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr|section|article|ul|ol)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _first_department(job):
    for department in job.get("departments") or []:
        name = department.get("name") if isinstance(department, dict) else department
        if name:
            return name
    return None


def _office_locations(job):
    locations = []
    seen = set()
    for office in job.get("offices") or []:
        name = office.get("name") if isinstance(office, dict) else office
        if name and name not in seen:
            locations.append(name)
            seen.add(name)
    return locations


def _apply_pay(job, payload):
    ranges = job.get("pay_input_ranges") or []
    currencies = {r.get("currency_type") for r in ranges if r.get("currency_type")}
    if len(currencies) != 1:
        return
    mins = [r.get("min_cents") for r in ranges if r.get("min_cents") is not None]
    maxs = [r.get("max_cents") for r in ranges if r.get("max_cents") is not None]
    if mins:
        payload["salary_min"] = min(mins) / 100
    if maxs:
        payload["salary_max"] = max(maxs) / 100
    payload["salary_currency"] = next(iter(currencies))
    periods = [r.get("pay_frequency") or r.get("interval") for r in ranges if r.get("pay_frequency") or r.get("interval")]
    if periods and len(set(periods)) == 1:
        payload["salary_period"] = periods[0]


def crawl(ctx):
    url = SITE
    if "pay_transparency=true" not in url:
        sep = "&" if "?" in url else "?"
        url = url + sep + "pay_transparency=true"

    data = json.loads(ctx.fetch(url))
    for job in data.get("jobs", []):
        title = job.get("title")
        description = _clean_text(job.get("content"))
        location_obj = job.get("location") or {}
        location = location_obj.get("name") if isinstance(location_obj, dict) else location_obj
        canonical_url = job.get("absolute_url") or job.get("url")
        if not (canonical_url and title and description and location):
            continue

        payload = {
            "url": canonical_url,
            "title": title,
            "company": "Databricks",
            "location": location,
            "description": description,
            "source": SITE,
        }

        if job.get("id") is not None:
            payload["external_id"] = str(job.get("id"))
        if job.get("first_published"):
            payload["posted_at"] = job.get("first_published")
        department = _first_department(job)
        if department:
            payload["department"] = department
        locations = _office_locations(job)
        if locations:
            payload["locations"] = locations
        if job.get("absolute_url"):
            payload["apply_url"] = job.get("absolute_url")

        _apply_pay(job, payload)

        yield {k: v for k, v in payload.items() if v is not None and v != ""}
