
import html
import json
import re
from urllib.parse import urljoin

SITE = "https://jobs.blockchaincapital.com/"
EXTRA_HOSTS = []
SITEMAP_URL = urljoin(SITE, "sitemap.xml")
MAX_JOBS = 3

def _text_from_html(value):
    if not value:
        return None
    value = html.unescape(str(value))
    value = re.sub(r"(?is)<(script|style).*?</\1>", " ", value)
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr|ul|ol)\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s*", "\n", value)
    return value.strip() or None

def _first(*values):
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, dict)) and not value:
            continue
        return value
    return None

def _clean(value):
    if value is None:
        return None
    value = html.unescape(str(value)).strip()
    return value or None

def _json_from_script(page, script_id):
    pattern = r'<script[^>]+id=["\']' + re.escape(script_id) + r'["\'][^>]*>(.*?)</script>'
    match = re.search(pattern, page, re.S | re.I)
    if not match:
        return None
    try:
        return json.loads(html.unescape(match.group(1)))
    except Exception:
        return None

def _current_job(page):
    data = _json_from_script(page, "__NEXT_DATA__")
    if not isinstance(data, dict):
        return None
    job = data.get("props", {}).get("pageProps", {}).get("initialState", {}).get("jobs", {}).get("currentJob")
    return job if isinstance(job, dict) else None

def _jsonld_job(page):
    match = re.search(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page, re.S | re.I)
    if not match:
        return None
    try:
        data = json.loads(html.unescape(match.group(1)))
    except Exception:
        return None
    if isinstance(data, dict) and data.get("@type") == "JobPosting":
        return data
    return None

def _location_from_ld(ld):
    loc = ld.get("jobLocation") if isinstance(ld, dict) else None
    if isinstance(loc, dict):
        address = loc.get("address")
        if isinstance(address, dict):
            parts = []
            for key in ("addressLocality", "addressRegion", "addressCountry"):
                value = address.get(key)
                if isinstance(value, dict):
                    value = value.get("name")
                if value:
                    parts.append(str(value))
            if parts:
                return ", ".join(parts)
        return loc.get("name")
    if isinstance(loc, list):
        names = []
        for item in loc:
            name = _location_from_ld({"jobLocation": item})
            if name:
                names.append(name)
        return ", ".join(names) if names else None
    return None

def _salary_from_job(job):
    if not job.get("compensationPublic"):
        return None
    low = job.get("compensationAmountMinCents")
    high = job.get("compensationAmountMaxCents")
    if low is None and high is None:
        return None
    parts = []
    if job.get("compensationCurrency"):
        parts.append(str(job.get("compensationCurrency")))
    def money(cents):
        try:
            return "%g" % (float(cents) / 100.0)
        except Exception:
            return str(cents)
    if low is not None and high is not None:
        parts.append("%s-%s" % (money(low), money(high)))
    elif low is not None:
        parts.append(money(low))
    elif high is not None:
        parts.append(money(high))
    if job.get("compensationPeriod"):
        parts.append(str(job.get("compensationPeriod")))
    return " ".join(parts) if parts else None

def _salary_from_ld(ld):
    base = ld.get("baseSalary") if isinstance(ld, dict) else None
    if not isinstance(base, dict):
        return None
    value = base.get("value")
    parts = []
    if base.get("currency"):
        parts.append(str(base.get("currency")))
    if isinstance(value, dict):
        low = value.get("minValue")
        high = value.get("maxValue")
        if low is not None and high is not None:
            parts.append("%s-%s" % (low, high))
        elif low is not None:
            parts.append(str(low))
        elif high is not None:
            parts.append(str(high))
        if value.get("unitText"):
            parts.append(str(value.get("unitText")))
    elif value is not None:
        parts.append(str(value))
    return " ".join(parts) if parts else None

def _make_item(url, page):
    job = _current_job(page) or {}
    ld = _jsonld_job(page) or {}
    org = job.get("organization") if isinstance(job.get("organization"), dict) else {}
    hiring = ld.get("hiringOrganization") if isinstance(ld.get("hiringOrganization"), dict) else {}

    loc_names = []
    for loc in job.get("locations") or []:
        if isinstance(loc, dict):
            name = _first(loc.get("name"), loc.get("description"))
            if name:
                loc_names.append(str(name))
        elif loc:
            loc_names.append(str(loc))

    item = {
        "url": url,
        "title": _clean(_first(job.get("title"), ld.get("title"))),
        "company": _clean(_first(org.get("name"), hiring.get("name"))),
        "location": _clean(_first(", ".join(loc_names) if loc_names else None, _location_from_ld(ld))),
        "description": _first(_text_from_html(job.get("description")), _text_from_html(ld.get("description"))),
        "source": SITE,
    }

    optional = {
        "external_id": job.get("id"),
        "apply_url": job.get("url"),
        "posted_at": _first(job.get("postedAt"), ld.get("datePosted")),
        "expires_at": _first(job.get("expiresAt"), ld.get("validThrough")),
        "salary": _first(_salary_from_job(job), _salary_from_ld(ld)),
    }
    employment = _first(job.get("employmentTypes"), ld.get("employmentType"))
    if isinstance(employment, list):
        employment = ", ".join(str(x) for x in employment if x)
    optional["employment_type"] = employment

    functions = job.get("jobFunctions")
    if isinstance(functions, list):
        names = []
        for fn in functions:
            if isinstance(fn, dict):
                names.append(_first(fn.get("name"), fn.get("description")))
            elif fn:
                names.append(str(fn))
        optional["department"] = ", ".join(x for x in names if x) if names else None

    for key, value in optional.items():
        value = _clean(value)
        if value is not None:
            item[key] = value

    if all(item.get(k) for k in ("url", "title", "company", "location", "description")):
        return item
    return None

def _sitemap_jobs(xml):
    entries = []
    for block in re.findall(r"<url\b[^>]*>(.*?)</url>", xml, re.S | re.I):
        loc = re.search(r"<loc>(.*?)</loc>", block, re.S | re.I)
        if not loc:
            continue
        url = html.unescape(loc.group(1)).strip()
        if "/jobs/" not in url:
            continue
        lastmod = re.search(r"<lastmod>(.*?)</lastmod>", block, re.S | re.I)
        entries.append(((lastmod.group(1).strip() if lastmod else ""), url))
    entries.sort(reverse=True)
    seen = set()
    for _, url in entries:
        if url not in seen:
            seen.add(url)
            yield url

def crawl(ctx):
    sitemap = ctx.fetch(SITEMAP_URL)
    for index, url in enumerate(_sitemap_jobs(sitemap)):
        if index >= MAX_JOBS:
            break
        page = ctx.fetch(url)
        item = _make_item(url, page)
        if item:
            yield item
