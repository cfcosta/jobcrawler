import html as _html
import re
from urllib.parse import urljoin

# Chargebee runs its careers site on SAP SuccessFactors "jobs2web" (Career Site
# Builder). The listing at /search/ is server-rendered HTML: a results table of
# <tr class="data-row"> rows plus a "Results X to Y of N" total, paginated via
# ?startrow=N (25 per page). Each job detail page carries schema.org microdata
# (datePosted, hiringOrganization, address) and the body in itemprop="description".
SITE = "https://jobs.chargebee.com/search/"
EXTRA_HOSTS = []

_PAGE_SIZE = 25
_MAX_START = 5000  # hard safety cap on pagination

_TOTAL_RE = re.compile(r"Results\s+\d+\s+to\s+\d+\s+of\s+([\d,]+)", re.I)
_ROW_RE = re.compile(r'<tr\s+class="data-row">(.*?)</tr>', re.S | re.I)
_JOB_HREF_RE = re.compile(r'href="(/job/[^"]+)"', re.I)
_TITLE_RE = re.compile(r"<a[^>]*jobTitle-link[^>]*>(.*?)</a>", re.S | re.I)
_LOCATION_CELL_RE = re.compile(r'class="colLocation[^"]*"[^>]*>(.*?)</td>', re.S | re.I)
_FACILITY_CELL_RE = re.compile(r'class="colFacility[^"]*"[^>]*>(.*?)</td>', re.S | re.I)
_ID_RE = re.compile(r"/job/[^/]+/(\d+)")

_DESC_START_RE = re.compile(r'<span[^>]*itemprop="description"[^>]*>', re.I)
_APPLY_RE = re.compile(r'dialogApplyBtn[^>]*href="([^"]+)"', re.I)
_DATE_RE = re.compile(r'itemprop="datePosted"[^>]*content="([^"]+)"', re.I)
_GEO_RE = re.compile(r'class="jobGeoLocation"[^>]*>(.*?)</span>', re.S | re.I)
_H1_RE = re.compile(r'<h1[^>]*itemprop="title"[^>]*>(.*?)</h1>', re.S | re.I)

_MONTHS = {
    "Jan": "01",
    "Feb": "02",
    "Mar": "03",
    "Apr": "04",
    "May": "05",
    "Jun": "06",
    "Jul": "07",
    "Aug": "08",
    "Sep": "09",
    "Oct": "10",
    "Nov": "11",
    "Dec": "12",
}
# e.g. "Wed Jul 08 02:00:00 UTC 2026"
_JAVA_DATE_RE = re.compile(r"\b(\w{3})\s+(\d{1,2})\s+[\d:]+\s+\w+\s+(\d{4})")

_BLOCK_RE = re.compile(
    r"</?(?:p|div|br|li|ul|ol|tr|h[1-6]|hr|table|section)\b[^>]*>", re.I
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_LINE_RE = re.compile(r"[ \t]+")
_MULTI_NL_RE = re.compile(r"\n{3,}")


def _strip_html(fragment):
    if not fragment:
        return ""
    text = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", fragment, flags=re.S | re.I)
    text = _BLOCK_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = _html.unescape(text)
    text = text.replace("\r", "\n").replace("\xa0", " ")
    text = _WS_LINE_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _MULTI_NL_RE.sub("\n\n", text)
    return text.strip()


def _inline(fragment):
    """Collapse an HTML fragment to a single clean line (row cells, titles)."""
    text = _html.unescape(_TAG_RE.sub(" ", fragment or ""))
    return re.sub(r"\s+", " ", text).strip()


def _balanced_span(page, start_match):
    """Return the inner HTML of the <span> opened at start_match, balancing nesting."""
    depth = 0
    for m in re.finditer(r"<(/?)span\b", page[start_match.start() :], re.I):
        if not m.group(1):
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                inner_start = start_match.end()
                inner_end = start_match.start() + m.start()
                return page[inner_start:inner_end]
    return page[start_match.end() :]


def _iso_date(raw):
    m = _JAVA_DATE_RE.search(raw or "")
    if not m:
        return None
    month = _MONTHS.get(m.group(1))
    if not month:
        return None
    return "%s-%s-%02d" % (m.group(3), month, int(m.group(2)))


def _workplace_type(*parts):
    blob = " ".join(p for p in parts if p).lower()
    if "hybrid" in blob:
        return "Hybrid"
    if "remote" in blob:
        return "Remote"
    return None


def _parse_rows(page):
    rows = []
    for row_html in _ROW_RE.findall(page):
        href = _JOB_HREF_RE.search(row_html)
        title = _TITLE_RE.search(row_html)
        if not (href and title):
            continue
        loc = _LOCATION_CELL_RE.search(row_html)
        dept = _FACILITY_CELL_RE.search(row_html)
        rows.append(
            {
                "path": href.group(1),
                "title": _inline(title.group(1)),
                "location": _inline(loc.group(1)) if loc else "",
                "department": _inline(dept.group(1)) if dept else "",
            }
        )
    return rows


def _build(ctx, row):
    url = urljoin(SITE, row["path"])
    try:
        page = ctx.fetch(url)
    except Exception as exc:
        ctx.log("chargebee: detail fetch failed", url, exc)
        return None

    desc_open = _DESC_START_RE.search(page)
    description = _strip_html(_balanced_span(page, desc_open)) if desc_open else ""

    title = row["title"]
    if not title:
        h1 = _H1_RE.search(page)
        if h1:
            title = _inline(h1.group(1))

    location = row["location"]
    if not location:
        geo = _GEO_RE.search(page)
        if geo:
            location = _inline(geo.group(1))

    if not (title and url and description and location):
        return None

    out = {
        "url": url,
        "title": title,
        "company": "Chargebee",
        "location": location,
        "description": description,
        "source": SITE,
    }

    ext_id = _ID_RE.search(row["path"])
    if ext_id:
        out["external_id"] = ext_id.group(1)

    apply_m = _APPLY_RE.search(page)
    if apply_m:
        out["apply_url"] = urljoin(url, _html.unescape(apply_m.group(1)))

    if row["department"]:
        out["department"] = row["department"]

    date_m = _DATE_RE.search(page)
    if date_m:
        posted = _iso_date(date_m.group(1)) or _inline(date_m.group(1))
        if posted:
            out["posted_at"] = posted

    workplace = _workplace_type(location, title)
    if workplace:
        out["workplace_type"] = workplace

    out["locations"] = [location]
    return out


def crawl(ctx):
    seen = set()
    startrow = 0
    total = None
    while True:
        url = SITE if startrow == 0 else "%s?startrow=%d" % (SITE, startrow)
        try:
            page = ctx.fetch(url)
        except Exception as exc:
            ctx.log("chargebee: listing fetch failed", url, exc)
            break

        if total is None:
            tm = _TOTAL_RE.search(page)
            if tm:
                total = int(tm.group(1).replace(",", ""))

        new_on_page = 0
        for row in _parse_rows(page):
            if row["path"] in seen:
                continue
            seen.add(row["path"])
            new_on_page += 1
            job = _build(ctx, row)
            if job:
                yield job

        if new_on_page == 0:
            break
        startrow += _PAGE_SIZE
        if total is not None and startrow >= total:
            break
        if startrow > _MAX_START:
            break
