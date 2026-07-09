
import re
import html
import json

SITE = "https://www.rippling.com/careers"
EXTRA_HOSTS = []

SITEMAP_URL = "https://www.rippling.com/sitemap.xml"

def _strip_tags(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?is)<(script|style|svg|noscript).*?</\1>", " ", value)
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|section|article|tr)\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None

def _meta(html_text, name):
    pats = [
        r'<meta[^>]+name=["\']%s["\'][^>]+content=["\']([^"\']*)["\']' % re.escape(name),
        r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+name=["\']%s["\']' % re.escape(name),
        r'<meta[^>]+property=["\']%s["\'][^>]+content=["\']([^"\']*)["\']' % re.escape(name),
        r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+property=["\']%s["\']' % re.escape(name),
    ]
    for pat in pats:
        m = re.search(pat, html_text, re.I)
        if m and m.group(1).strip():
            return html.unescape(m.group(1)).strip()
    return None

def _title(html_text):
    m = re.search(r"<title>(.*?)</title>", html_text, re.I | re.S)
    if m:
        title = _strip_tags(m.group(1))
        if title:
            title = re.sub(r"\s*\|\s*Rippling.*$", "", title).strip()
            return title
    return _meta(html_text, "og:title")

def _description(html_text):
    # Prefer main content when available; fall back to body text.
    m = re.search(r"(?is)<main[^>]*>(.*?)</main>", html_text)
    text = _strip_tags(m.group(1) if m else html_text)
    if not text:
        text = _meta(html_text, "description")
    if text:
        # Trim obvious global chrome while preserving page-specific content.
        text = re.sub(r"(?is)^.*?(Careers|Open roles|Engineering)", r"\1", text, count=1)
        return text[:20000].strip()
    return None

def crawl(ctx):
    sitemap = ctx.fetch(SITEMAP_URL)
    urls = []
    for url in re.findall(r"<loc>(https://www\.rippling\.com/[^<]+)</loc>", sitemap):
        if re.search(r"/(?:[a-z]{2}-[A-Z]{2}/)?careers(?:/engineering)?/?$", url):
            urls.append(url)

    # Include the noindex listing page, which is the board entry point but absent from the sitemap.
    urls.insert(0, "https://www.rippling.com/careers/open-roles")

    seen = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        page = ctx.fetch(url)
        title = _title(page)
        desc = _description(page)
        if not title or not desc:
            continue
        item = {
            "url": url,
            "title": title,
            "company": "Rippling",
            "location": "Multiple locations",
            "description": desc,
            "source": SITE,
        }
        yield item
