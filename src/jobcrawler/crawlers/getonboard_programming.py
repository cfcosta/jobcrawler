
import re
import html
from urllib.parse import urljoin

SITE = "https://www.getonbrd.com/jobs/programming"
EXTRA_HOSTS = []
MAX_JOBS = 15


def _clean_text(value):
    value = html.unescape(value or "")
    value = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(p|div|li|ul|ol|h[1-6]|section)>", "\n", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _first(pattern, text, flags=re.I | re.S):
    match = re.search(pattern, text or "", flags)
    return html.unescape(match.group(1)).strip() if match else None


def _fetch(ctx, url):
    try:
        return ctx.fetch(url)
    except TypeError:
        return ctx.fetch(url, "GET", "")


def _job_urls(listing_html):
    seen = set()
    urls = []
    pattern = r'(?:https://www\.getonbrd\.com)?/(?:empleos|jobs)/(?:programacion|programming)/[^"\'<>\s)]+'
    for match in re.finditer(pattern, listing_html or ""):
        url = html.unescape(match.group(0))
        if url.startswith("/"):
            url = urljoin(SITE, url)
        url = re.sub(r"[?#].*$", "", url).rstrip("/")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _description(detail_html):
    marker = re.search(r'<div[^>]+id=["\']job-body["\'][^>]*>', detail_html or "", re.I)
    if not marker:
        return None
    start = marker.end()
    end = len(detail_html)
    for token in (
        '<div class="gb-tags"',
        '<div class="gb-breadcrumbs',
        '<footer',
        'id="related_jobs"',
        'widget_related_jobs',
    ):
        pos = detail_html.find(token, start)
        if pos != -1:
            end = min(end, pos)
    return _clean_text(detail_html[start:end]) or None


def _header_chunk(detail_html):
    body = detail_html.lower().find("<body")
    desc = detail_html.find('id="job-body"')
    if body == -1:
        body = 0
    if desc == -1:
        desc = min(len(detail_html), body + 80000)
    return detail_html[body:desc]


def _meta_description(detail_html):
    for pattern in (
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']description["\']',
    ):
        value = _first(pattern, detail_html)
        if value:
            return value
    return None


def _title_company_location(detail_html):
    header = _header_chunk(detail_html)
    title = _first(r'<span[^>]+itemprop=["\']title["\'][^>]*>(.*?)</span>', header)
    company = _first(r'<strong[^>]+itemprop=["\']name["\'][^>]*>(.*?)</strong>', header)

    if not title or not company:
        og_title = _first(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', detail_html)
        if og_title:
            if not title:
                title = og_title.split(" at ", 1)[0].split(" - ", 1)[0].strip()
            if not company and " at " in og_title:
                company = re.split(r"\s+-\s+", og_title.split(" at ", 1)[1], 1)[0].strip()

    location = None
    loc_block = _first(r'<span[^>]+itemprop=["\']jobLocation["\'][^>]*>(.*?)<span class=["\']mx-3["\']', header)
    if loc_block:
        location = _clean_text(loc_block)
        location = re.sub(r"Position is .*", "", location, flags=re.I | re.S).strip()
        location = re.sub(r"\n+", " ", location).strip()
    if not location:
        desc = _meta_description(detail_html)
        match = re.search(r"\bjob in ([^:]+):", desc or "", re.I)
        if match:
            location = match.group(1).strip()

    return title, company, location


def _employment_type(detail_html):
    return _first(r'<span[^>]+itemprop=["\']employmentType["\'][^>]*>(.*?)</span>', detail_html)


def _posted_at(detail_html):
    return _first(r'<time[^>]+datetime=["\']([^"\']+)["\'][^>]+itemprop=["\']datePosted["\']', detail_html)


def _apply_url(detail_html):
    href = _first(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>\s*Apply now\s*</a>', detail_html)
    if not href:
        href = _first(r'<a[^>]+href=["\']([^"\']+/applications/new[^"\']*)["\']', detail_html)
    return urljoin(SITE, href) if href else None


def _salary(detail_html):
    out = {}
    base = re.search(r'<span[^>]+itemprop=["\']baseSalary["\'][^>]*>(.*?)</h3>', detail_html or "", re.I | re.S)
    if not base:
        return out
    block = base.group(1)

    minv = _first(r'content=["\']([0-9]+(?:\.[0-9]+)?)["\'][^>]+itemprop=["\']minValue["\']', block)
    maxv = _first(r'content=["\']([0-9]+(?:\.[0-9]+)?)["\'][^>]+itemprop=["\']maxValue["\']', block)
    period = _first(r'content=["\']([^"\']+)["\'][^>]+itemprop=["\']unitText["\']', block)
    currency = _first(r'content=["\']([A-Z]{3})["\'][^>]+itemprop=["\']currency["\']', block)

    printed = _clean_text(block)
    printed = re.sub(r"The salary shown.*?currency\.", "", printed, flags=re.I | re.S).strip()
    if printed and re.search(r"\d", printed) and len(printed) < 120:
        out["salary"] = printed
    if minv:
        out["salary_min"] = float(minv)
    if maxv:
        out["salary_max"] = float(maxv)
    if currency:
        out["salary_currency"] = currency
    if period:
        out["salary_period"] = period
    return out


def _workplace_type(location):
    text = location or ""
    if re.search(r"remote|home", text, re.I):
        return "remote"
    if re.search(r"hybrid|partly", text, re.I):
        return "hybrid"
    if re.search(r"in-office|office|on-site", text, re.I):
        return "on-site"
    return None


def _job_kwargs(url, detail_html):
    title, company, location = _title_company_location(detail_html)
    kwargs = {
        "url": url,
        "title": title,
        "company": company,
        "location": location,
        "description": _description(detail_html),
        "source": SITE,
        "external_id": url.rstrip("/").rsplit("/", 1)[-1],
    }
    for key, value in (
        ("employment_type", _employment_type(detail_html)),
        ("posted_at", _posted_at(detail_html)),
        ("apply_url", _apply_url(detail_html)),
        ("workplace_type", _workplace_type(location)),
    ):
        if value:
            kwargs[key] = value
    kwargs.update(_salary(detail_html))
    return {key: value for key, value in kwargs.items() if value not in (None, "")}


def crawl(ctx):
    listing_html = _fetch(ctx, SITE)
    for url in _job_urls(listing_html)[:MAX_JOBS]:
        detail_html = _fetch(ctx, url)
        job = _job_kwargs(url, detail_html)
        if job.get("title") and job.get("company") and job.get("location") and job.get("description"):
            yield job
