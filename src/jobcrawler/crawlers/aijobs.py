import html
import re
from urllib.parse import urljoin, urlparse

SITE = "https://aijobs.net/"
EXTRA_HOSTS = []

MAX_POSTINGS = 6

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_JOB_LINK_RE = re.compile(r'href="(/job/[a-z0-9-]+-\d+/)"')
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)
_LOC_RE = re.compile(r'<div class="col">\s*<strong>(.*?)</strong>', re.S)
_COMPANY_RE = re.compile(
    r'<a class="text-decoration-none d-block text-break" href="/company/([a-z0-9-]+)/"'
)
_COMPANY_ID_RE = re.compile(r"-\d+$")
_CANON_RE = re.compile(r'<link rel="canonical" href="([^"]+)"')
_BADGE_BLOCK_RE = re.compile(r'<p class="py-4">(.*?)</p>', re.S)
_BADGE_RE = re.compile(
    r'<span class="text-bg-(secondary|warning)[^"]*">(.*?)</span>', re.S
)
_MAIN_RE = re.compile(r"<main[^>]*>(.*?)</main>", re.S)
_ID_TAIL_RE = re.compile(r"-(\d+)/?$")
_DIGIT_RE = re.compile(r"\d")

_SCRIPT_RE = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1>")
_BR_RE = re.compile(r"(?i)<br\s*/?>")
_BLOCK_CLOSE_RE = re.compile(r"(?i)</(?:p|div|li|ul|ol|h[1-6]|tr|section)>")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_INLINE_RE = re.compile(r"[ \t\r\f\v]+")
_WS_NL_RE = re.compile(r" *\n[ \n]*")


def _text(value):
    if not value:
        return None
    value = _SCRIPT_RE.sub(" ", value)
    value = _BR_RE.sub("\n", value)
    value = _BLOCK_CLOSE_RE.sub("\n", value)
    value = _TAG_RE.sub(" ", value)
    value = html.unescape(value)
    value = _WS_INLINE_RE.sub(" ", value)
    value = _WS_NL_RE.sub("\n", value)
    return value.strip() or None


def _company(page):
    m = _COMPANY_RE.search(page)
    if not m:
        return None
    slug = _COMPANY_ID_RE.sub("", m.group(1)).replace("-", " ").strip()
    return slug.title() or None


def _badges(page):
    block = _BADGE_BLOCK_RE.search(page)
    salary = None
    employment = None
    if block:
        for m in _BADGE_RE.finditer(block.group(1)):
            if m.group(1) != "secondary":
                continue
            val = _text(m.group(2))
            if not val:
                continue
            if _DIGIT_RE.search(val):
                salary = val
            else:
                employment = val
    return salary, employment


def _description(page):
    m = _MAIN_RE.search(page)
    if not m:
        return None
    body = m.group(1)
    start = body.find("<h5")
    if start == -1:
        return None
    end = body.find('<div class="row g-2">', start)
    if end == -1:
        end = body.find("Related jobs", start)
    if end == -1:
        end = len(body)
    return _text(body[start:end])


def _detail(ctx, url):
    page = ctx.fetch(url, headers=_HEADERS)
    h1 = _H1_RE.search(page)
    if not h1:
        return None
    title = _text(h1.group(1))
    loc = _LOC_RE.search(page, h1.end())
    location = _text(loc.group(1)) if loc else None
    company = _company(page)
    description = _description(page)
    if not (title and location and company and description):
        return None
    canon = _CANON_RE.search(page)
    canonical = canon.group(1) if canon else url
    salary, employment = _badges(page)
    out = {
        "url": canonical,
        "title": title,
        "company": company,
        "location": location,
        "description": description,
        "source": "aijobs",
    }
    ext = _ID_TAIL_RE.search(urlparse(canonical).path)
    if ext:
        out["external_id"] = ext.group(1)
    if salary:
        out["salary"] = salary
    if employment:
        out["employment_type"] = employment
    if location.lower() == "remote":
        out["workplace_type"] = "Remote"
    return out


def crawl(ctx):
    page = ctx.fetch(SITE, headers=_HEADERS)
    seen = set()
    urls = []
    for m in _JOB_LINK_RE.finditer(page):
        url = urljoin(SITE, m.group(1))
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    for url in urls[:MAX_POSTINGS]:
        try:
            row = _detail(ctx, url)
        except Exception:
            continue
        if row:
            yield row
