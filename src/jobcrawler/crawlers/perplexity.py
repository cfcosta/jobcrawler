
import json
import re
from html import unescape

SITE = "https://api.ashbyhq.com/posting-api/job-board/perplexity?includeCompensation=true"
EXTRA_HOSTS = []

COMPANY = "Perplexity AI"


def _clean_html(value):
    if not value:
        return None
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|ul|ol)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _compact(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _employment_type(value):
    mapping = {
        "FullTime": "Full-time",
        "PartTime": "Part-time",
        "Contract": "Contract",
        "Temporary": "Temporary",
        "Internship": "Internship",
    }
    value = _compact(value)
    return mapping.get(value, value)


def _location(job):
    locations = []
    primary = _compact(job.get("location"))
    if primary:
        locations.append(primary)
    for item in job.get("secondaryLocations") or []:
        loc = _compact(item.get("location") if isinstance(item, dict) else item)
        if loc and loc not in locations:
            locations.append(loc)
    return ", ".join(locations) if locations else None


def _money(value, currency):
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int):
        amount = f"{value:,}"
    else:
        amount = str(value)
    return f"{currency} {amount}" if currency else amount


def _salary(compensation):
    components = (compensation or {}).get("summaryComponents") or []
    parts = []
    for component in components:
        if not isinstance(component, dict):
            continue
        minimum = component.get("minValue")
        maximum = component.get("maxValue")
        currency = _compact(component.get("currencyCode"))
        interval = _compact(component.get("interval"))
        label = _compact(component.get("label")) or _compact(component.get("type"))

        if minimum is None and maximum is None:
            continue

        low = _money(minimum, currency)
        high = _money(maximum, currency)
        if low and high:
            text = f"{low} - {high}"
        else:
            text = low or high

        if interval:
            text = f"{text} / {interval}"
        if label:
            text = f"{label}: {text}"
        parts.append(text)

    return "; ".join(parts) if parts else None


def crawl(ctx):
    raw = ctx.fetch(SITE)
    data = json.loads(raw)
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue

        description = _compact(job.get("descriptionPlain")) or _clean_html(job.get("descriptionHtml"))
        title = _compact(job.get("title"))
        url = _compact(job.get("jobUrl"))
        location = _location(job)

        if not (title and url and location and description):
            continue

        item = {
            "url": url,
            "title": title,
            "company": COMPANY,
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": _compact(job.get("id")),
            "employment_type": _employment_type(job.get("employmentType")),
            "workplace_type": _compact(job.get("workplaceType")),
            "department": _compact(job.get("department")) or _compact(job.get("team")),
            "posted_at": _compact(job.get("publishedAt")),
            "apply_url": _compact(job.get("applyUrl")),
        }

        salary = _salary(job.get("compensation"))
        if salary:
            item["salary"] = salary

        yield {key: value for key, value in item.items() if value is not None}
