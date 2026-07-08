
import html
import json
import re
from urllib.parse import urljoin

SITE = "https://jobs.framework.ventures/"
EXTRA_HOSTS = []
MAX_JOBS = 5

def _text_from_html(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6])\s*>", "\n", value)
    value = re.sub(r"(?i)<\s*li[^>]*>", "\n- ", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None

def _next_data(page):
    match = re.search(r'<script id=["\']__NEXT_DATA__["\'] type=["\']application/json["\']>(.*?)</script>', page, re.I | re.S)
    if not match:
        return {}
    try:
        return json.loads(html.unescape(match.group(1)))
    except Exception:
        return {}

def _jsonld_jobs(page):
    pattern = r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>'
    for match in re.finditer(pattern, page, re.I | re.S):
        try:
            loaded = json.loads(html.unescape(match.group(1)).strip())
        except Exception:
            continue
        objects = loaded if isinstance(loaded, list) else loaded.get("@graph", [loaded]) if isinstance(loaded, dict) else []
        for obj in objects:
            if isinstance(obj, dict) and obj.get("@type") == "JobPosting":
                yield obj

def _locations_from(value):
    if not value:
        return None
    parts = []
    for item in value if isinstance(value, list) else [value]:
        name = None
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            name = item.get("description") or item.get("name")
            address = item.get("address")
            if not name and isinstance(address, dict):
                vals = [address.get("addressLocality"), address.get("addressRegion"), address.get("addressCountry")]
                name = ", ".join([v for v in vals if v])
        if name and name not in parts:
            parts.append(name)
    return "; ".join(parts) or None

def _names_from(value):
    if not value:
        return None
    if isinstance(value, str):
        return value
    names = []
    for item in value:
        name = item if isinstance(item, str) else item.get("name") or item.get("description") if isinstance(item, dict) else None
        if name and name not in names:
            names.append(name)
    return ", ".join(names) or None

def _salary(current):
    if not current.get("compensationPublic"):
        return None
    low = current.get("compensationAmountMinCents")
    high = current.get("compensationAmountMaxCents")
    if low is None and high is None:
        return None
    pieces = []
    for amount in (low, high):
        if amount is not None and amount not in pieces:
            value = str(amount / 100).rstrip("0").rstrip(".")
            pieces.append(value)
    salary = " - ".join(pieces)
    currency = current.get("compensationCurrency")
    period = current.get("compensationPeriod")
    if currency:
        salary = currency + " " + salary
    if period and period != "period_not_defined":
        salary = salary + " " + period
    return salary or None

def _job_from_page(url, page):
    data = _next_data(page)
    current = data.get("props", {}).get("pageProps", {}).get("initialState", {}).get("jobs", {}).get("currentJob", {})
    if isinstance(current, dict) and current.get("title"):
        org = current.get("organization") or {}
        job = {
            "url": url,
            "title": current.get("title"),
            "company": org.get("name") or "Framework Ventures",
            "location": _locations_from(current.get("locations")),
            "description": _text_from_html(current.get("description")),
            "source": SITE,
            "external_id": str(current.get("id")) if current.get("id") is not None else None,
            "posted_at": current.get("postedAt"),
            "expires_at": current.get("expiresAt"),
            "employment_type": _names_from(current.get("employmentTypes")),
            "department": _names_from(current.get("jobFunctions")),
            "apply_url": current.get("url") or current.get("applicationPath"),
            "salary": _salary(current),
        }
        return {k: v for k, v in job.items() if v not in (None, "", [])}

    for item in _jsonld_jobs(page):
        org = item.get("hiringOrganization") or {}
        job = {
            "url": url,
            "title": item.get("title"),
            "company": org.get("name") if isinstance(org, dict) else "Framework Ventures",
            "location": _locations_from(item.get("jobLocation")),
            "description": _text_from_html(item.get("description")),
            "source": SITE,
            "posted_at": item.get("datePosted"),
            "expires_at": item.get("validThrough"),
            "employment_type": _names_from(item.get("employmentType")),
        }
        return {k: v for k, v in job.items() if v not in (None, "", [])}
    return None

def _sitemap_urls(ctx, sitemap_url):
    xml = ctx.fetch(sitemap_url)
    return re.findall(r"<loc>\s*([^<]+?)\s*</loc>", xml)

def crawl(ctx):
    yielded = 0
    index_url = urljoin(SITE, "sitemap.xml")
    for sitemap_url in _sitemap_urls(ctx, index_url):
        if "/sitemap_jobs" not in sitemap_url:
            continue
        for job_url in _sitemap_urls(ctx, sitemap_url):
            page = ctx.fetch(job_url)
            job = _job_from_page(job_url, page)
            if job and job.get("title") and job.get("description"):
                yield job
                yielded += 1
                if yielded >= MAX_JOBS:
                    return
