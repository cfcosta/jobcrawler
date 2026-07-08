
import json
import re
import html

SITE = 'https://api.ashbyhq.com/posting-api/job-board/baseten?includeCompensation=true'
EXTRA_HOSTS = []

def _html_to_text(value):
    text = html.unescape(value or "")
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|section|ul|ol)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()

def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value

def _location(job):
    locations = []
    value = _clean(job.get("location"))
    if value:
        locations.append(value)
    for item in job.get("secondaryLocations") or []:
        if isinstance(item, dict):
            value = _clean(item.get("location") or item.get("name"))
        else:
            value = _clean(item)
        if value:
            locations.append(value)
    seen = set()
    unique = []
    for loc in locations:
        low = loc.lower()
        if low not in seen:
            seen.add(low)
            unique.append(loc)
    return "; ".join(unique) if unique else None

def _compensation(job):
    comp = job.get("compensation") or {}
    if not isinstance(comp, dict):
        return {}
    components = comp.get("summaryComponents") or []
    if not components:
        return {}
    nums = []
    currency = None
    interval = None
    for item in components:
        if not isinstance(item, dict):
            continue
        for name in ("minValue", "maxValue"):
            value = item.get(name)
            if isinstance(value, (int, float)):
                nums.append(value)
        currency = currency or _clean(item.get("currencyCode"))
        interval = interval or _clean(item.get("interval"))
    out = {}
    if nums:
        out["salary_min"] = min(nums)
        out["salary_max"] = max(nums)
    if currency:
        out["salary_currency"] = currency
    if interval:
        out["salary_interval"] = interval
    return out

def crawl(ctx):
    raw = ctx.fetch(SITE)
    data = json.loads(raw)
    for job in data.get("jobs", []):
        if not isinstance(job, dict):
            continue
        title = _clean(job.get("title"))
        url = _clean(job.get("jobUrl")) or (SITE.split("?")[0].rstrip("/") + "/" + str(job.get("id")))
        description = _clean(job.get("descriptionPlain")) or _html_to_text(job.get("descriptionHtml"))
        location = _location(job)
        if not (title and url and description and location):
            continue

        item = {
            "url": url,
            "title": title,
            "company": "Baseten",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": _clean(job.get("id")),
        }

        optional_map = {
            "employment_type": job.get("employmentType"),
            "workplace_type": job.get("workplaceType"),
            "department": job.get("department") or job.get("team"),
            "apply_url": job.get("applyUrl"),
            "posted_at": job.get("publishedAt") or job.get("createdAt"),
            "expires_at": job.get("expiresAt"),
        }
        for key, value in optional_map.items():
            value = _clean(value)
            if value is not None:
                item[key] = value

        for key, value in _compensation(job).items():
            if value is not None:
                item[key] = value

        yield item
