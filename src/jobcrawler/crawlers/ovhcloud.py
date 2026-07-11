import re
import html
from urllib.parse import urljoin, urlparse, parse_qsl, urlencode, urlunparse

# SAP SuccessFactors Career Site Builder board, hosted on OVHcloud's own domain.
# The search endpoint renders job tiles server-side (100 per page, paginated by startrow);
# each tile links to a detail page carrying schema.org/JobPosting microdata.
SITE = "https://careers.ovhcloud.com/search/?q=&locale=en_US"
EXTRA_HOSTS = []

_PAGE_SIZE = 100
_MAX_PAGES = 40

_MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ],
        1,
    )
}


def _clean_text(value):
    value = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", value or "")
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(p|div|li|ul|ol|h[1-6]|tr|span)\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _clean_inline(value):
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _search_url(startrow):
    parts = urlparse(SITE)
    params = dict(parse_qsl(parts.query, keep_blank_values=True))
    if startrow:
        params["startrow"] = str(startrow)
    else:
        params.pop("startrow", None)
    return urlunparse(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            parts.params,
            urlencode(params),
            parts.fragment,
        )
    )


def _total_count(page):
    m = re.search(r"of\s+([\d,]+)\s+Jobs", page, re.I)
    if m:
        try:
            return int(m.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


def _iter_tiles(page):
    """Yield (job_url, tile_title) for each job tile on a search-results page."""
    for tile in re.split(r'(?=<li[^>]*class="[^"]*\bjob-tile\b)', page):
        mu = re.search(r'data-url="([^"]+)"', tile)
        if not mu:
            continue
        href = html.unescape(mu.group(1))
        mt = re.search(
            r'<span[^>]*class="[^"]*\btitle\b[^"]*"[^>]*role="heading"[^>]*>(.*?)</span>',
            tile,
            re.I | re.S,
        )
        if not mt:
            mt = re.search(
                r'<span[^>]*class="[^"]*\bsection-title\b[^"]*"[^>]*>(.*?)</span>',
                tile,
                re.I | re.S,
            )
        title = _clean_inline(mt.group(1)) if mt else None
        yield urljoin(SITE, href), title


def _span_property(page, prop):
    m = re.search(
        r'<span\b[^>]*data-careersite-propertyid=["\']%s["\'][^>]*>(.*?)</span>'
        % re.escape(prop),
        page,
        re.I | re.S,
    )
    return _clean_text(m.group(1)) if m else None


def _meta_item(page, prop):
    m = re.search(
        r'<meta\b[^>]*itemprop=["\']%s["\'][^>]*content=["\']([^"\']*)["\']'
        % re.escape(prop),
        page,
        re.I,
    )
    return html.unescape(m.group(1)).strip() if m else None


def _title(page, fallback):
    if fallback:
        return fallback
    m = re.search(r"<title>(.*?)</title>", page, re.I | re.S)
    if not m:
        return None
    t = _clean_inline(m.group(1))
    t = re.sub(r"\s*Job Details\s*\|\s*OVHcloud\s*$", "", t, flags=re.I)
    t = re.sub(r"\s*\|\s*OVHcloud\s*$", "", t, flags=re.I)
    return t.strip() or None


def _description(page):
    idx = page.find('itemprop="description"')
    if idx < 0:
        idx = page.find("itemprop='description'")
    if idx < 0:
        return None
    chunk = page[idx:]
    start = chunk.find(">")
    if start >= 0:
        chunk = chunk[start + 1 :]
    for marker in (
        "<a xml:lang=",
        '<a class="btn',
        '<p class="job-location"',
        'class="applylink',
        '<div class="social',
    ):
        stop = chunk.find(marker)
        if stop > 1000:
            chunk = chunk[:stop]
            break
    text = _clean_text(chunk)
    text = re.sub(r'^itemprop=["\']description["\'][^\n]*\n?', "", text).strip()
    return text or None


def _location(page):
    loc = _span_property(page, "location")
    if not loc:
        loc = _meta_item(page, "streetAddress")
    return loc or None


def _posted_at(page):
    raw = _meta_item(page, "datePosted")
    if not raw:
        return None
    m = re.match(r"\w{3}\s+(\w{3})\s+(\d{1,2})\s+[\d:]+\s+\w+\s+(\d{4})", raw)
    if m:
        mon = _MONTHS.get(m.group(1))
        if mon:
            return "%04d-%02d-%02d" % (int(m.group(3)), mon, int(m.group(2)))
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        return m.group(0)
    return None


def _apply_url(page, job_url):
    m = re.search(
        r'<a\b[^>]*class=["\'][^"\']*\bapply\b[^"\']*["\'][^>]*href=["\']([^"\']+)["\']',
        page,
        re.I | re.S,
    )
    if not m:
        m = re.search(r'href=["\'](/talentcommunity/apply/[^"\']+)["\']', page, re.I)
    return urljoin(job_url, html.unescape(m.group(1))) if m else None


def _job_id_from_url(url):
    m = re.search(r"/(\d+)/?(?:\?|$)", url)
    return m.group(1) if m else None


def crawl(ctx):
    seen_jobs = set()
    total = None

    for page_index in range(_MAX_PAGES):
        startrow = page_index * _PAGE_SIZE
        page = ctx.fetch(_search_url(startrow))

        if total is None:
            total = _total_count(page)

        new_on_page = 0
        for job_url, tile_title in _iter_tiles(page):
            if job_url in seen_jobs:
                continue
            seen_jobs.add(job_url)
            new_on_page += 1

            detail = ctx.fetch(job_url)
            title = _title(detail, tile_title)
            description = _description(detail)
            if not job_url or not title or not description:
                continue

            location = _location(detail)

            item = {
                "source": SITE,
                "url": job_url,
                "title": title,
                "company": "OVHcloud",
                "description": description,
            }
            if location:
                item["location"] = location
                item["locations"] = [location]

            department = _span_property(detail, "department")
            if department:
                item["department"] = department

            employment_type = _span_property(detail, "customfield1")
            if employment_type:
                item["employment_type"] = employment_type

            workplace = _span_property(detail, "customfield5")
            if workplace:
                item["workplace_type"] = workplace

            posted = _posted_at(detail)
            if posted:
                item["posted_at"] = posted

            external_id = _job_id_from_url(job_url)
            if external_id:
                item["external_id"] = external_id

            apply_url = _apply_url(detail, job_url)
            if apply_url:
                item["apply_url"] = apply_url

            yield item

        if new_on_page == 0:
            break
        if total is not None and len(seen_jobs) >= total:
            break
