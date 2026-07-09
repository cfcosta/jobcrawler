
import json
import re
import html
from urllib.parse import urljoin

SITE = "https://jobs.electriccapital.com/jobs"
EXTRA_HOSTS = []
MAX_JOBS = 6

def _clean_text(value):
    if not value:
        return None
    value = html.unescape(str(value))
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(p|div|li|h[1-6]|section|article|tr)>", "\n", value)
    value = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s*", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    return value or None

def _first_json_ld_job(page):
    for block in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page, re.I | re.S):
        try:
            obj = json.loads(html.unescape(block))
        except Exception:
            continue
        candidates = obj if isinstance(obj, list) else [obj]
        for item in candidates:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return {}

def _next_current_job(page):
    m = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', page, re.I | re.S)
    if not m:
        return {}
    try:
        data = json.loads(html.unescape(m.group(1)))
    except Exception:
        return {}
    return data.get("props", {}).get("pageProps", {}).get("initialState", {}).get("jobs", {}).get("currentJob", {}) or {}

def _location_from_ld(ld):
    loc = ld.get("jobLocation")
    if isinstance(loc, list):
        parts = []
        for item in loc:
            val = _location_from_ld({"jobLocation": item})
            if val:
                parts.append(val)
        return ", ".join(dict.fromkeys(parts)) or None
    if isinstance(loc, dict):
        address = loc.get("address")
        if isinstance(address, dict):
            bits = []
            for key in ("addressLocality", "addressRegion", "addressCountry"):
                val = address.get(key)
                if isinstance(val, dict):
                    val = val.get("name")
                if val:
                    bits.append(str(val))
            if bits:
                return ", ".join(bits)
        if loc.get("name"):
            return str(loc.get("name"))
    return None

def _salary_from_job(cj):
    if not cj.get("compensationPublic"):
        return None
    cur = cj.get("compensationCurrency")
    lo = cj.get("compensationAmountMinCents")
    hi = cj.get("compensationAmountMaxCents")
    period = cj.get("compensationPeriod")
    if lo is None and hi is None:
        return None
    def money(cents):
        amount = cents / 100.0
        return str(int(amount)) if amount.is_integer() else ("%.2f" % amount)
    if lo is not None and hi is not None and lo != hi:
        text = "%s-%s" % (money(lo), money(hi))
    else:
        text = money(lo if lo is not None else hi)
    if cur:
        text = "%s %s" % (cur, text)
    if period and period != "period_not_defined":
        text = "%s / %s" % (text, period)
    return text

def _add_if(data, key, value):
    if value is None:
        return
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return
    if isinstance(value, list) and not value:
        return
    data[key] = value

def _sitemap_job_urls(sitemap):
    entries = re.findall(r"<url>\s*<loc>\s*([^<]+?/companies/[^<]+?/jobs/[^<]+?)\s*</loc>.*?<lastmod>\s*([^<]*)\s*</lastmod>.*?</url>", sitemap, re.I | re.S)
    entries = [(html.unescape(url).strip(), lastmod.strip()) for url, lastmod in entries]
    entries.sort(key=lambda item: item[1], reverse=True)
    seen = set()
    urls = []
    for url, lastmod in entries:
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls

def crawl(ctx):
    sitemap = ctx.fetch(urljoin(SITE, "/sitemap.xml"))
    for url in _sitemap_job_urls(sitemap)[:MAX_JOBS]:
        page = ctx.fetch(url)
        ld = _first_json_ld_job(page)
        cj = _next_current_job(page)

        status = (cj.get("status") or "").lower()
        visibility = (cj.get("visibility") or "").lower()
        if status and status != "active":
            continue
        if visibility and visibility != "visible":
            continue

        org = cj.get("organization") if isinstance(cj.get("organization"), dict) else {}
        ld_org = ld.get("hiringOrganization") if isinstance(ld.get("hiringOrganization"), dict) else {}

        title = cj.get("title") or ld.get("title")
        company = org.get("name") or ld_org.get("name")
        locations = cj.get("locations") if isinstance(cj.get("locations"), list) else []
        location = ", ".join([x.get("name") for x in locations if isinstance(x, dict) and x.get("name")])
        location = location or _location_from_ld(ld)
        description = _clean_text(cj.get("description") or ld.get("description"))

        if not (title and company and location and description):
            continue

        data = {
            "url": url,
            "title": str(title).strip(),
            "company": str(company).strip(),
            "location": str(location).strip(),
            "description": description,
            "source": SITE,
        }

        _add_if(data, "external_id", str(cj.get("id")) if cj.get("id") is not None else None)
        _add_if(data, "posted_at", cj.get("postedAt") or ld.get("datePosted"))
        _add_if(data, "expires_at", cj.get("expiresAt") or ld.get("validThrough"))
        _add_if(data, "apply_url", cj.get("url") or cj.get("applicationPath"))
        _add_if(data, "salary", _salary_from_job(cj))

        emp = cj.get("employmentTypes") or ld.get("employmentType")
        if isinstance(emp, list):
            emp = ", ".join([str(x.get("name") if isinstance(x, dict) else x) for x in emp if (x.get("name") if isinstance(x, dict) else x)])
        _add_if(data, "employment_type", emp)

        funcs = cj.get("jobFunctions")
        if isinstance(funcs, list):
            dept = ", ".join([str(x.get("name")) for x in funcs if isinstance(x, dict) and x.get("name")])
            _add_if(data, "department", dept)

        yield data
