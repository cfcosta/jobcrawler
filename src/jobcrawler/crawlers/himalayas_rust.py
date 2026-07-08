
import html
import json
import re
from datetime import datetime, timezone

SITE = "https://himalayas.app/jobs/api/search?q=rust&sort=recent"
EXTRA_HOSTS = []

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")

def _text_from_html(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|section|article|ul|ol)\s*>", "\n", value)
    value = _TAG_RE.sub(" ", value)
    value = html.unescape(value)
    value = _SPACE_RE.sub(" ", value).strip()
    return value or None

def _iso_from_epoch(value):
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()
    except Exception:
        return None

def _location(job):
    parts = []
    locs = job.get("locationRestrictions") or []
    if isinstance(locs, list):
        parts.extend(str(x) for x in locs if x not in (None, ""))
    elif locs:
        parts.append(str(locs))
    zones = job.get("timezoneRestrictions") or []
    if zones:
        if isinstance(zones, list):
            parts.append("Time zones: " + ", ".join(str(x) for x in zones))
        else:
            parts.append("Time zones: " + str(zones))
    return "; ".join(parts) if parts else "Remote"

def _salary(job):
    min_salary = job.get("minSalary")
    max_salary = job.get("maxSalary")
    currency = job.get("currency")
    period = job.get("salaryPeriod")
    if min_salary is None and max_salary is None:
        return None
    bits = []
    if currency:
        bits.append(str(currency))
    if min_salary is not None and max_salary is not None:
        if min_salary == max_salary:
            bits.append(str(min_salary))
        else:
            bits.append(str(min_salary) + "-" + str(max_salary))
    elif min_salary is not None:
        bits.append("from " + str(min_salary))
    elif max_salary is not None:
        bits.append("up to " + str(max_salary))
    if period:
        bits.append(str(period))
    return " ".join(bits) or None

def crawl(ctx):
    raw = ctx.fetch(SITE)
    data = json.loads(raw)
    seen = set()
    for item in data.get("jobs") or []:
        url = item.get("applicationLink") or item.get("guid")
        title = item.get("title")
        company = item.get("companyName")
        description = _text_from_html(item.get("description"))
        if not url or not title or not company or not description or url in seen:
            continue
        seen.add(url)
        out = {
            "url": url,
            "title": title,
            "company": company,
            "location": _location(item),
            "description": description,
            "source": SITE,
        }
        optional = {
            "employment_type": item.get("employmentType"),
            "department": ", ".join(item.get("parentCategories") or []) or None,
            "posted_at": _iso_from_epoch(item.get("pubDate")),
            "expires_at": _iso_from_epoch(item.get("expiryDate")),
            "external_id": item.get("guid"),
            "apply_url": item.get("applicationLink"),
            "salary": _salary(item),
        }
        if item.get("locationRestrictions") or item.get("timezoneRestrictions"):
            optional["workplace_type"] = "Remote"
        for key, value in optional.items():
            if value not in (None, "", [], {}):
                out[key] = value
        yield out
