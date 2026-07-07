import html
import re

SITE = "https://careers.datadoghq.com/engineering/"
EXTRA_HOSTS = []

SITEMAP = "https://careers.datadoghq.com/sitemap.xml"
COMPANY = "Datadog"
MAX_DETAILS = 7

_DETAIL_RE = re.compile(r"https://careers\.datadoghq\.com/detail/(\d+)/")
_MAIN_RE = re.compile(r"<main\b[^>]*>(.*?)</main>", re.S | re.I)
_H2_RE = re.compile(r"<h2\b[^>]*>(.*?)</h2>", re.S | re.I)
_TITLE_TAG_RE = re.compile(r"<title>(.*?)</title>", re.S | re.I)
_TITLE_SUFFIX_RE = re.compile(r"\s*\|\s*Datadog Careers\s*$", re.I)
_LOC_RE = re.compile(r'</h2>(.*?)<div class="job-description"', re.S | re.I)
_DESC_START_RE = re.compile(r'<div class="job-description"[^>]*>', re.I)
_DESC_END_RE = re.compile(r'<div id="grnhse_app"', re.I)

_BR_RE = re.compile(r"(?i)<\s*br\s*/?\s*>")
_BLOCK_RE = re.compile(r"(?i)</\s*(?:p|div|li|h[1-6]|ul|ol|tr|section)\s*>")
_TAG_RE = re.compile(r"<[^>]+>")
_INLINE_WS_RE = re.compile(r"[ \t\r\f\v]+")
_NL_WS_RE = re.compile(r"\n[ \t]+")
_MULTI_NL_RE = re.compile(r"\n{3,}")


def _clean(fragment):
    if not fragment:
        return None
    text = _BR_RE.sub("\n", fragment)
    text = _BLOCK_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _INLINE_WS_RE.sub(" ", text)
    text = _NL_WS_RE.sub("\n", text)
    text = _MULTI_NL_RE.sub("\n\n", text)
    return text.strip() or None


def _detail_ids(sitemap):
    seen = set()
    ids = []
    for match in _DETAIL_RE.finditer(sitemap):
        jid = match.group(1)
        if jid in seen:
            continue
        seen.add(jid)
        ids.append(jid)
    ids.sort(key=int, reverse=True)
    return ids


def _title(page, body):
    match = _H2_RE.search(body)
    title = _clean(match.group(1)) if match else None
    if title:
        return title
    match = _TITLE_TAG_RE.search(page)
    if not match:
        return None
    return _clean(_TITLE_SUFFIX_RE.sub("", html.unescape(match.group(1)).strip()))


def _description(body):
    start = _DESC_START_RE.search(body)
    if not start:
        return None
    end = _DESC_END_RE.search(body, start.end())
    fragment = body[start.start() : end.start()] if end else body[start.start() :]
    return _clean(fragment)


def _parse(page):
    main = _MAIN_RE.search(page)
    if not main:
        return None
    body = main.group(1)
    title = _title(page, body)
    loc = _LOC_RE.search(body)
    location = _clean(loc.group(1)) if loc else None
    description = _description(body)
    if not (title and location and description):
        return None
    return title, location, description


def crawl(ctx):
    sitemap = ctx.fetch(SITEMAP)
    for jid in _detail_ids(sitemap)[:MAX_DETAILS]:
        url = f"https://careers.datadoghq.com/detail/{jid}/"
        try:
            page = ctx.fetch(url)
        except Exception:
            continue
        parsed = _parse(page)
        if not parsed:
            continue
        title, location, description = parsed
        yield {
            "url": url,
            "title": title,
            "company": COMPANY,
            "location": location,
            "description": description,
            "source": "datadog",
            "external_id": jid,
        }
