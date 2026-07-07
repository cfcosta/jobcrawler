import html
import re
from urllib.parse import urlsplit

SITE = "https://quickwit.io/jobs/distributed-software-engineer"
EXTRA_HOSTS = []

_PARTS = urlsplit(SITE)
_BASE = f"{_PARTS.scheme}://{_PARTS.netloc}"
_SITEMAP = f"{_BASE}/sitemap.xml"
_ROLE_PREFIX = f"{_BASE}/jobs/"

_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
_MAIN = re.compile(r"<main[^>]*>(.*?)</main>", re.I | re.S)
_TOC = re.compile(r'<div[^>]*class="[^"]*col--2', re.I)
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
_OGTITLE = re.compile(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"', re.I)
_MAILTO = re.compile(r'href="mailto:([^"?\s]+)', re.I)
_OPENFOR = re.compile(r"this position is open for\s+(.+?)\.", re.I)
_WORKIN = re.compile(r"working in\s+(.+)$", re.I)
_FULLTIME = re.compile(r"full[- ]time position", re.I)
_COMP = re.compile(r"compensation range for the role is\s+(.+?)\s+and follows", re.I)
_REMOTEISH = re.compile(r"opt-in|work from|fully remote|distributed team", re.I)

_SCRIPT = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1>")
_BR = re.compile(r"(?i)<\s*br\s*/?\s*>")
_BLOCK = re.compile(r"(?i)</\s*(p|div|li|h[1-6]|section|tr|ul|ol|blockquote)\s*>")
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")
_NL = re.compile(r" *\n *")
_MULTINL = re.compile(r"\n{3,}")


def _clean(fragment):
    if not fragment:
        return None
    text = _SCRIPT.sub("", fragment)
    text = _BR.sub("\n", text)
    text = _BLOCK.sub("\n", text)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    text = text.replace("​", "")
    text = _WS.sub(" ", text)
    text = _NL.sub("\n", text)
    text = _MULTINL.sub("\n\n", text)
    return text.strip() or None


def _title(main, page):
    m = _H1.search(main)
    title = _clean(m.group(1)) if m else None
    if title:
        return title
    og = _OGTITLE.search(page)
    if og:
        return html.unescape(og.group(1)).rsplit("|", 1)[0].strip() or None
    return None


def _location(description):
    m = _OPENFOR.search(description)
    if m:
        who = m.group(1).strip()
        region = _WORKIN.search(who)
        region = region.group(1).strip() if region else who
        return f"Remote ({region})" if region else "Remote"
    if _REMOTEISH.search(description):
        return "Remote"
    return None


def _parse(url, page):
    body = _MAIN.search(page)
    if not body:
        return None
    main = _TOC.split(body.group(1))[0]
    title = _title(main, page)
    description = _clean(main)
    if not title or title == "Page Not Found":
        return None
    if not description or len(description) < 200:
        return None
    location = _location(description)
    if not location:
        return None

    item = {
        "url": url,
        "title": title,
        "company": "Quickwit",
        "location": location,
        "description": description,
        "source": "quickwit",
        "external_id": url.rstrip("/").rsplit("/", 1)[-1],
    }
    if _FULLTIME.search(description):
        item["employment_type"] = "Full-time"
    comp = _COMP.search(description)
    if comp:
        item["salary"] = comp.group(1).strip()
    mail = _MAILTO.search(page)
    if mail:
        item["apply_url"] = f"mailto:{mail.group(1).strip()}"
    return item


def _role_urls(ctx):
    urls = [SITE]
    for loc in _LOC.findall(ctx.fetch(_SITEMAP)):
        loc = html.unescape(loc.strip())
        if not loc.startswith(_ROLE_PREFIX):
            continue
        tail = loc[len(_ROLE_PREFIX) :].strip("/")
        if tail and "/" not in tail and loc not in urls:
            urls.append(loc)
    return urls[:50]


def crawl(ctx):
    for url in _role_urls(ctx):
        item = _parse(url, ctx.fetch(url))
        if item:
            yield item
