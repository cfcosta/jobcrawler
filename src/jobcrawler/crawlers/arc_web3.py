
import re
import json
import html
from datetime import datetime, timezone
from urllib.parse import urljoin

SITE = "https://arc.dev/remote-jobs/blockchain"
EXTRA_HOSTS = []


def _text(value):
    if value is None:
        return None
    value = html.unescape(str(value))
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|strong)\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s*", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = value.strip()
    return value or None


def _next_data(page):
    m = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', page, re.S | re.I)
    if not m:
        return {}
    return json.loads(html.unescape(m.group(1)))


def _iso_from_epoch(value):
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()
    except Exception:
        return None


def _job_type(value):
    if not value:
        return None
    mapping = {
        "permanent": "Full-time",
        "contract": "Contract",
        "part_time": "Part-time",
        "part-time": "Part-time",
    }
    return mapping.get(str(value).lower(), str(value))


def _location(job):
    countries = job.get("requiredCountries") or []
    if countries:
        loc = ", ".join(str(c) for c in countries if c)
        if loc:
            return loc
    loc = job.get("location") or job.get("locations")
    if isinstance(loc, str) and loc:
        return loc
    if isinstance(loc, dict):
        name = loc.get("name") or loc.get("displayName")
        if name:
            return name
    return "Remote"


def _salary(job):
    mn_a, mx_a = job.get("minAnnualSalary"), job.get("maxAnnualSalary")
    if mn_a is not None or mx_a is not None:
        if mn_a is not None and mx_a is not None:
            mn = float(mn_a)
            mx = float(mx_a)
            return f"US ${mn:,.0f} - {mx:,.0f} per year"
        if mn_a is not None:
            mn = float(mn_a)
            return f"US ${mn:,.0f}+ per year"
        mx = float(mx_a)
        return f"Up to US ${mx:,.0f} per year"

    mn_h, mx_h = job.get("minHourlyRate"), job.get("maxHourlyRate")
    if mn_h is not None or mx_h is not None:
        if mn_h is not None and mx_h is not None:
            mn = float(mn_h)
            mx = float(mx_h)
            return f"US ${mn:,.0f} - {mx:,.0f} per hour"
        if mn_h is not None:
            mn = float(mn_h)
            return f"US ${mn:,.0f}+ per hour"
        mx = float(mx_h)
        return f"Up to US ${mx:,.0f} per hour"
    return None


def _make_job(url, detail):
    data = _next_data(detail)
    pp = (data.get("props") or {}).get("pageProps") or {}
    job = pp.get("job") or {}
    if not job:
        return None

    title = _text(job.get("title"))
    company = _text(job.get("companyName") or (job.get("company") or {}).get("name") or (pp.get("company") or {}).get("name"))
    description = _text(job.get("description"))
    if not (title and company and description):
        return None

    item = {
        "url": url,
        "title": title,
        "company": company,
        "location": _location(job),
        "description": description,
        "source": SITE,
    }

    optional = {
        "external_id": job.get("randomKey"),
        "apply_url": job.get("url"),
        "posted_at": _iso_from_epoch(job.get("postedAt")),
        "employment_type": _job_type(job.get("jobType")),
        "workplace_type": "Remote",
        "department": job.get("positionType") or job.get("jobRole"),
        "salary": _salary(job),
    }
    for key, value in optional.items():
        if value not in (None, ""):
            item[key] = value

    return item


def crawl(ctx):
    listing = ctx.fetch(SITE + "?__nextDataReq=1")
    seen = set()

    # Detail pages are same-host and contain pageProps.job.description, unlike listing cards.
    for href in re.findall(r'href=["\']([^"\']*/remote-jobs/j/[^"\']+)["\']', listing):
        url = urljoin(SITE, html.unescape(href))
        if url in seen:
            continue
        seen.add(url)
        try:
            detail = ctx.fetch(url)
        except Exception:
            continue
        job = _make_job(url, detail)
        if job is not None:
            yield job
