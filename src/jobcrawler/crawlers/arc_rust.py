
import html
import json
import re
from urllib.parse import urljoin

SITE = "https://arc.dev/remote-jobs/rust"
EXTRA_HOSTS = []

def _next_data(text):
    m = re.search(r'<script\b[^>]*\bid=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', text, re.S | re.I)
    if not m:
        return {}
    try:
        return json.loads(html.unescape(m.group(1)))
    except Exception:
        return {}

def _clean_html(value):
    if value is None:
        return None
    text = str(value)
    text = re.sub(r'(?i)<\s*br\s*/?\s*>', '\n', text)
    text = re.sub(r'(?i)</\s*(p|div|li|h[1-6]|tr|section|article)\s*>', '\n', text)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html.unescape(text)
    text = re.sub(r'[ \t\r\f\v]+', ' ', text)
    text = re.sub(r'\n\s*\n\s*', '\n\n', text)
    return text.strip() or None

def _location(job, company=None):
    countries = job.get("requiredCountries") or job.get("required_locations") or []
    if isinstance(countries, list) and countries:
        return "Remote - " + ", ".join(str(x) for x in countries if x)
    req_locs = job.get("requiredLocations") or []
    if isinstance(req_locs, list) and req_locs:
        names = []
        for item in req_locs:
            if isinstance(item, dict):
                names.append(item.get("name") or item.get("country") or item.get("countryCode"))
            else:
                names.append(item)
        names = [str(x) for x in names if x]
        if names:
            return "Remote - " + ", ".join(names)
    hq = (company or {}).get("headquarters") if isinstance(company, dict) else None
    if isinstance(hq, dict) and hq.get("name"):
        return "Remote - " + str(hq.get("name"))
    return "Remote"

def _company(job, company):
    if isinstance(company, dict) and company.get("name"):
        return company.get("name")
    c = job.get("company")
    if isinstance(c, dict):
        return c.get("name") or c.get("urlString") or "Arc"
    return job.get("companyName") or "Arc"

def _employment_type(job):
    value = job.get("jobType") or job.get("contractType")
    if not value:
        return None
    mapping = {
        "permanent": "Full-time",
        "full_time": "Full-time",
        "full-time": "Full-time",
        "contract": "Contract",
        "freelance": "Contract",
        "part_time": "Part-time",
        "part-time": "Part-time",
    }
    return mapping.get(str(value).lower(), str(value))

def _salary(job):
    parts = []
    min_annual = job.get("minAnnualSalary")
    max_annual = job.get("maxAnnualSalary")
    if min_annual is not None or max_annual is not None:
        if min_annual is not None and max_annual is not None:
            parts.append("$%s-$%s per year" % (min_annual, max_annual))
        elif min_annual is not None:
            parts.append("From $%s per year" % min_annual)
        else:
            parts.append("Up to $%s per year" % max_annual)
    min_hourly = job.get("minHourlyRate")
    max_hourly = job.get("maxHourlyRate")
    if min_hourly is not None or max_hourly is not None:
        if min_hourly is not None and max_hourly is not None:
            parts.append("$%s-$%s per hour" % (min_hourly, max_hourly))
        elif min_hourly is not None:
            parts.append("From $%s per hour" % min_hourly)
        else:
            parts.append("Up to $%s per hour" % max_hourly)
    return "; ".join(parts) or None

def _make_job(ctx, url):
    text = ctx.fetch(url)
    data = _next_data(text)
    props = data.get("props", {}).get("pageProps", {})
    job = props.get("job") or {}
    if not isinstance(job, dict) or not job.get("title") or job.get("closed"):
        return None
    desc = _clean_html(job.get("description"))
    if not desc:
        return None
    company_obj = props.get("company") if isinstance(props.get("company"), dict) else None
    kwargs = {
        "url": url,
        "title": str(job.get("title")).strip(),
        "company": _company(job, company_obj),
        "location": _location(job, company_obj),
        "description": desc,
        "source": SITE,
    }
    salary = _salary(job)
    if salary:
        kwargs["salary"] = salary
    employment_type = _employment_type(job)
    if employment_type:
        kwargs["employment_type"] = employment_type
    kwargs["workplace_type"] = "remote"
    if job.get("jobRole") or job.get("positionType"):
        kwargs["department"] = job.get("jobRole") or job.get("positionType")
    if job.get("postedAt") is not None:
        kwargs["posted_at"] = str(job.get("postedAt"))
    if job.get("randomKey"):
        kwargs["external_id"] = str(job.get("randomKey"))
    if job.get("url"):
        kwargs["apply_url"] = str(job.get("url"))
    elif job.get("jobLink"):
        kwargs["apply_url"] = str(job.get("jobLink"))
    return kwargs

def crawl(ctx):
    seen = set()
    # The unparameterized category page is the authoritative Rust landing page.
    for listing_url in [SITE, SITE + "?jobPage=0"]:
        listing = ctx.fetch(listing_url)
        for href in re.findall(r'<a\b[^>]*class=["\'][^"\']*job-title[^"\']*["\'][^>]*href=["\']([^"\']+)["\']', listing, re.I):
            url = urljoin(SITE, html.unescape(href))
            if url in seen:
                continue
            seen.add(url)
            job = _make_job(ctx, url)
            if job is not None:
                yield job
