
import html
import json
import re
from urllib.parse import urljoin

SITE = "https://jobs.btv.vc/"
EXTRA_HOSTS = []

MAX_JOBS = 6

def _text_from_html(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr|section|article|ul|ol)\s*>", "\n", value)
    value = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None

def _first_present(mapping, *keys):
    for key in keys:
        value = mapping.get(key)
        if value is not None and value != "":
            return value
    return None

def _locations(job):
    parts = []
    for loc in job.get("locations") or []:
        if isinstance(loc, dict):
            name = _first_present(loc, "name", "description")
        else:
            name = str(loc) if loc else None
        if name and name not in parts:
            parts.append(name)
    return "; ".join(parts) if parts else None

def _department(job):
    names = []
    for fn in job.get("jobFunctions") or []:
        if isinstance(fn, dict):
            name = fn.get("name")
        else:
            name = str(fn) if fn else None
        if name and name not in names:
            names.append(name)
    return "; ".join(names) if names else None

def _employment_type(job):
    names = []
    for item in job.get("employmentTypes") or []:
        if isinstance(item, dict):
            name = _first_present(item, "name", "description", "label")
        else:
            name = str(item) if item else None
        if name and name not in names:
            names.append(name)
    return "; ".join(names) if names else None

def _salary(job):
    if not job.get("compensationPublic"):
        return None
    min_cents = job.get("compensationAmountMinCents")
    max_cents = job.get("compensationAmountMaxCents")
    currency = job.get("compensationCurrency")
    period = job.get("compensationPeriod")
    if min_cents is None and max_cents is None:
        return None
    def money(cents):
        amount = cents / 100.0
        if amount.is_integer():
            return f"{int(amount):,}"
        return f"{amount:,.2f}"
    if min_cents is not None and max_cents is not None and min_cents != max_cents:
        text = f"{currency + ' ' if currency else ''}{money(min_cents)} - {money(max_cents)}"
    else:
        cents = min_cents if min_cents is not None else max_cents
        text = f"{currency + ' ' if currency else ''}{money(cents)}"
    if period and period != "period_not_defined":
        text += f" / {period}"
    if job.get("compensationOffersEquity") is True:
        text += " + equity"
    return text

def _job_urls_from_sitemap(xml):
    entries = []
    for block in re.findall(r"<url>\s*(.*?)\s*</url>", xml or "", re.S):
        loc_match = re.search(r"<loc>\s*(.*?)\s*</loc>", block, re.S)
        lastmod_match = re.search(r"<lastmod>\s*(.*?)\s*</lastmod>", block, re.S)
        if not loc_match:
            continue
        loc = html.unescape(loc_match.group(1).strip())
        if "/companies/" in loc and "/jobs/" in loc:
            lastmod = lastmod_match.group(1).strip() if lastmod_match else ""
            entries.append((lastmod, loc))
    # The sitemap is already newest-first, but this preserves recency if ordering changes.
    entries.sort(key=lambda item: item[0], reverse=True)
    return [loc for _, loc in entries]

def _current_job(page_html):
    match = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', page_html or "", re.S)
    if not match:
        return None
    data = json.loads(html.unescape(match.group(1)))
    return (((data.get("props") or {}).get("pageProps") or {}).get("initialState") or {}).get("jobs", {}).get("currentJob")

def crawl(ctx):
    sitemap_url = urljoin(SITE, "sitemap.xml")
    sitemap = ctx.fetch(sitemap_url)
    for detail_url in _job_urls_from_sitemap(sitemap)[:MAX_JOBS]:
        page = ctx.fetch(detail_url)
        job = _current_job(page)
        if not isinstance(job, dict):
            continue
        if job.get("status") and job.get("status") != "active":
            continue
        title = job.get("title")
        description = _text_from_html(job.get("description"))
        organization = job.get("organization") or {}
        company = organization.get("name")
        location = _locations(job)
        if not (title and company and location and description):
            continue

        item = {
            "url": detail_url,
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "source": SITE,
        }

        optional = {
            "salary": _salary(job),
            "employment_type": _employment_type(job),
            "department": _department(job),
            "posted_at": job.get("postedAt"),
            "expires_at": job.get("expiresAt"),
            "external_id": str(job["id"]) if job.get("id") is not None else None,
            "apply_url": job.get("url") or job.get("applicationPath"),
        }
        for key, value in optional.items():
            if value is not None and value != "":
                item[key] = value

        yield item
