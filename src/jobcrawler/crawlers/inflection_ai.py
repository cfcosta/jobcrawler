
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/inflectionai/jobs?content=true"
EXTRA_HOSTS = []


def _html_to_text(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|tr|h[1-6]|ul|ol)\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li[^>]*>", "\n- ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _first_name(items):
    if not items:
        return None
    for item in items:
        if isinstance(item, dict) and item.get("name"):
            return item.get("name")
    return None


def _office_locations(job):
    offices = job.get("offices") or []
    locations = []
    for office in offices:
        if not isinstance(office, dict):
            continue
        value = office.get("name")
        if value and value not in locations:
            locations.append(value)
    return locations or None


def _pay_fields(job):
    ranges = job.get("pay_input_ranges") or []
    currencies = set()
    for item in ranges:
        if isinstance(item, dict) and item.get("currency_type"):
            currencies.add(item.get("currency_type"))
    if not ranges or len(currencies) != 1:
        return {}

    mins = []
    maxs = []
    period = None
    for item in ranges:
        if not isinstance(item, dict):
            continue
        if item.get("min_cents") is not None:
            mins.append(item.get("min_cents") / 100)
        if item.get("max_cents") is not None:
            maxs.append(item.get("max_cents") / 100)
        if not period and item.get("pay_period"):
            period = item.get("pay_period")

    fields = {"salary_currency": list(currencies)[0]}
    if mins:
        fields["salary_min"] = min(mins)
    if maxs:
        fields["salary_max"] = max(maxs)
    if period:
        fields["salary_period"] = period
    return fields


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        location = (job.get("location") or {}).get("name")
        item = {
            "url": job.get("absolute_url"),
            "title": job.get("title"),
            "company": job.get("company_name") or "Inflection AI",
            "location": location,
            "description": _html_to_text(job.get("content")),
            "source": SITE,
            "external_id": str(job.get("id")) if job.get("id") is not None else None,
            "apply_url": job.get("absolute_url"),
            "posted_at": job.get("first_published"),
            "department": _first_name(job.get("departments") or []),
        }

        locations = _office_locations(job)
        if locations:
            item["locations"] = locations

        deadline = job.get("application_deadline")
        if deadline:
            item["expires_at"] = deadline

        item.update(_pay_fields(job))

        yield {key: value for key, value in item.items() if value is not None}
