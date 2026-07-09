
import html
import json
import re
from urllib.parse import urljoin

SITE = "https://jobs.polychain.capital/"
EXTRA_HOSTS = []
MAX_JOBS = 8

def _text_from_html(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|section|li|ul|ol|h[1-6])\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None

def _next_data(page):
    m = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', page, re.S | re.I)
    if not m:
        return None
    try:
        return json.loads(html.unescape(m.group(1)))
    except Exception:
        return None

def _current_job(page):
    data = _next_data(page)
    if not data:
        return None
    props = data.get("props") or {}
    page_props = props.get("pageProps") or {}
    initial = page_props.get("initialState") or {}
    jobs = initial.get("jobs") or {}
    return jobs.get("currentJob")

def _sitemap_job_urls(xml):
    entries = []
    for block in re.findall(r"<url>(.*?)</url>", xml, re.S | re.I):
        loc_m = re.search(r"<loc>(.*?)</loc>", block, re.S | re.I)
        if not loc_m:
            continue
        loc = html.unescape(loc_m.group(1)).strip()
        if "/companies/" not in loc or "/jobs/" not in loc:
            continue
        last_m = re.search(r"<lastmod>(.*?)</lastmod>", block, re.S | re.I)
        lastmod = html.unescape(last_m.group(1)).strip() if last_m else ""
        entries.append((lastmod, loc))
    entries.sort(reverse=True)
    seen = set()
    for _, loc in entries:
        if loc not in seen:
            seen.add(loc)
            yield loc

def _location(job):
    names = []
    for loc in job.get("locations") or []:
        if isinstance(loc, dict):
            name = loc.get("name") or loc.get("description")
            if name and name not in names:
                names.append(name)
    return ", ".join(names) or None

def _employment_type(job):
    values = []
    for item in job.get("employmentTypes") or []:
        if isinstance(item, dict):
            value = item.get("name") or item.get("description") or item.get("type")
        else:
            value = str(item) if item is not None else None
        if value and value not in values:
            values.append(value)
    return ", ".join(values) or None

def _salary(job):
    if not job.get("compensationPublic"):
        return None
    lo = job.get("compensationAmountMinCents")
    hi = job.get("compensationAmountMaxCents")
    cur = job.get("compensationCurrency")
    period = job.get("compensationPeriod")
    if lo is None and hi is None:
        return None

    if lo is not None and hi is not None and lo != hi:
        amount = "%g-%g" % (lo / 100.0, hi / 100.0)
    else:
        cents = lo if lo is not None else hi
        amount = "%g" % (cents / 100.0)

    parts = []
    if cur:
        parts.append(str(cur))
    parts.append(amount)
    if period and period != "period_not_defined":
        parts.append("per " + str(period))
    if job.get("compensationOffersEquity") is True:
        parts.append("+ equity")
    return " ".join(parts) or None

def _workplace_type(location):
    if location and "remote" in location.lower():
        return "remote"
    return None

def crawl(ctx):
    sitemap = ctx.fetch(urljoin(SITE, "sitemap.xml"))
    yielded = 0

    for url in _sitemap_job_urls(sitemap):
        if yielded >= MAX_JOBS:
            break

        page = ctx.fetch(url)
        job = _current_job(page)
        if not job or job.get("status") not in (None, "active"):
            continue

        title = job.get("title")
        description = _text_from_html(job.get("description"))
        org = job.get("organization") or {}
        company = org.get("name") or "Polychain Capital"
        location = _location(job) or "Unspecified"

        if not title or not description:
            continue

        item = {
            "url": url,
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "source": SITE,
        }

        external_id = job.get("id")
        if external_id is not None:
            item["external_id"] = str(external_id)

        posted_at = job.get("postedAt")
        if posted_at:
            item["posted_at"] = posted_at

        expires_at = job.get("expiresAt")
        if expires_at:
            item["expires_at"] = expires_at

        apply_url = job.get("url") or job.get("applicationPath")
        if apply_url:
            item["apply_url"] = apply_url

        employment_type = _employment_type(job)
        if employment_type:
            item["employment_type"] = employment_type

        workplace_type = _workplace_type(location)
        if workplace_type:
            item["workplace_type"] = workplace_type

        salary = _salary(job)
        if salary:
            item["salary"] = salary

        yielded += 1
        yield item
