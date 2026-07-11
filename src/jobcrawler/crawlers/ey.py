
import re
import html
from urllib.parse import urljoin, urlparse, parse_qsl, urlencode, urlunparse

SITE = "https://careers.ey.com/ey/search/?q=&locationsearch=Brazil&locale=en_US"
EXTRA_HOSTS = []

def _clean_text(value):
    value = re.sub(r'(?is)<(script|style|noscript).*?</\1>', ' ', value or '')
    value = re.sub(r'(?i)<br\s*/?>', '\n', value)
    value = re.sub(r'(?i)</(p|div|li|ul|ol|h[1-6]|tr|span)\s*>', '\n', value)
    value = re.sub(r'<[^>]+>', ' ', value)
    value = html.unescape(value).replace('\xa0', ' ')
    value = re.sub(r'[ \t\r\f\v]+', ' ', value)
    value = re.sub(r' *\n *', '\n', value)
    value = re.sub(r'\n{3,}', '\n\n', value)
    return value.strip()

def _search_url(startrow):
    parts = urlparse(SITE)
    params = dict(parse_qsl(parts.query, keep_blank_values=True))
    if startrow:
        params["startrow"] = str(startrow)
    else:
        params.pop("startrow", None)
    return urlunparse((parts.scheme, parts.netloc, parts.path, parts.params, urlencode(params), parts.fragment))

def _page_startrows(page):
    starts = {0}
    for raw in re.findall(r'[?&amp;]startrow=(\d+)', page):
        try:
            starts.add(int(raw))
        except ValueError:
            pass
    return starts

def _iter_rows(page):
    for row in re.findall(r'<tr\b[^>]*class=["\'][^"\']*data-row[^"\']*["\'][^>]*>(.*?)</tr>', page, re.I | re.S):
        m = re.search(r'<a\b[^>]*class=["\'][^"\']*jobTitle-link[^"\']*["\'][^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', row, re.I | re.S)
        if not m:
            continue
        href = html.unescape(m.group(1))
        title = _clean_text(m.group(2))
        lm = re.search(r'<span\b[^>]*class=["\'][^"\']*jobLocation[^"\']*["\'][^>]*>(.*?)</span>', row, re.I | re.S)
        location = _clean_text(lm.group(1)) if lm else None
        if location:
            location = re.sub(r'\s*\+\d+\s+more.*$', '', location, flags=re.I).strip()
        yield title, urljoin(SITE, href), location

def _meta_item(page, prop):
    m = re.search(r'<meta\b[^>]*itemprop=["\']%s["\'][^>]*content=["\']([^"\']*)["\']' % re.escape(prop), page, re.I)
    return html.unescape(m.group(1)).strip() if m else None

def _span_property(page, prop):
    m = re.search(r'<span\b[^>]*data-careersite-propertyid=["\']%s["\'][^>]*>(.*?)</span>' % re.escape(prop), page, re.I | re.S)
    return _clean_text(m.group(1)) if m else None

def _description(page):
    idx = page.find('itemprop="description"')
    if idx < 0:
        idx = page.find("itemprop='description'")
    if idx < 0:
        return None
    chunk = page[idx:]
    start = chunk.find('>')
    if start >= 0:
        chunk = chunk[start + 1:]
    for marker in ('<p class="job-location"', '<div class="customPlugin customPlugin-2725', '<a xml:lang=', '<a class="btn'):
        stop = chunk.find(marker)
        if stop > 1000:
            chunk = chunk[:stop]
            break
    text = _clean_text(chunk)
    text = re.sub(r'^itemprop=["\']description["\'][^\n]*\n?', '', text).strip()
    return text or None

def _locations(page, fallback):
    locs = []
    for addr in re.findall(r'<span[^>]*itemprop=["\']address["\'][^>]*>(.*?)</span>', page, re.I | re.S):
        parts = []
        for prop in ("addressLocality", "addressRegion", "addressCountry", "postalCode"):
            m = re.search(r'<meta\b[^>]*itemprop=["\']%s["\'][^>]*content=["\']([^"\']+)["\']' % prop, addr, re.I)
            if m:
                parts.append(html.unescape(m.group(1)).strip())
        loc = ", ".join(p for p in parts if p)
        if loc and loc not in locs:
            locs.append(loc)
    if not locs and fallback:
        locs.append(fallback)
    return locs

def _posted_at(page):
    return None

def _apply_url(page, job_url):
    m = re.search(r'<a\b[^>]*class=["\'][^"\']*\bapply\b[^"\']*["\'][^>]*href=["\']([^"\']+)["\']', page, re.I | re.S)
    return urljoin(job_url, html.unescape(m.group(1))) if m else None

def _salary(page):
    m = re.search(r'Remunera(?:ç|&ccedil;)ão:\s*</span>\s*<span[^>]*>(.*?)</span>', page, re.I | re.S)
    return _clean_text(m.group(1)) if m else None

def _job_id_from_url(url):
    m = re.search(r'/(\d+)/?$', url)
    return m.group(1) if m else None

def crawl(ctx):
    first = ctx.fetch(SITE)
    starts = sorted(_page_startrows(first))
    if not starts:
        starts = [0]

    seen_pages = set()
    seen_jobs = set()
    yielded = 0

    for start in starts:
        page_url = _search_url(start)
        if page_url in seen_pages:
            continue
        seen_pages.add(page_url)
        page = first if start == 0 else ctx.fetch(page_url)

        # If the first page exposed a truncated pagination window, pick up any later startrow links.
        for extra in sorted(_page_startrows(page)):
            if extra not in starts:
                starts.append(extra)
                starts.sort()

        for row_title, job_url, row_location in _iter_rows(page):
            if job_url in seen_jobs:
                continue
            seen_jobs.add(job_url)

            detail = ctx.fetch(job_url)
            title = _span_property(detail, "title") or row_title
            description = _description(detail)
            if not title or not description:
                continue

            locations = _locations(detail, row_location)
            location = locations[-1] if locations else row_location
            if not location:
                location = "Brazil"

            item = {
                "source": SITE,
                "url": job_url,
                "title": title,
                "company": "EY",
                "location": location,
                "locations": locations,
                "description": description,
            }

            posted = _posted_at(detail)
            if posted:
                item["posted_at"] = posted

            external_id = _span_property(detail, "customfield5") or _job_id_from_url(job_url)
            if external_id:
                item["external_id"] = external_id

            apply_url = _apply_url(detail, job_url)
            if apply_url:
                item["apply_url"] = apply_url

            salary = _salary(detail)
            if salary:
                item["salary"] = salary

            yield item
            yielded += 1
            if yielded >= 5:
                return
