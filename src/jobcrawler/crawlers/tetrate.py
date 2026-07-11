
import re
import html
from urllib.parse import urljoin

SITE = "https://careers.tetr8.io/"
EXTRA_HOSTS = []


def _strip_tags(fragment):
    fragment = re.sub(r"(?is)<(script|style|svg)\b.*?</\1>", " ", fragment)
    fragment = re.sub(r"(?i)<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"(?i)</(p|div|li|h1|h2|h3|h4|ul|ol)>", "\n", fragment)
    text = re.sub(r"(?s)<[^>]+>", " ", fragment)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _first(pattern, text, flags=re.I | re.S):
    m = re.search(pattern, text, flags)
    return html.unescape(m.group(1)).strip() if m else None


def _homepage_departments(ctx):
    departments = {}
    page = ctx.fetch(SITE)
    block = _first(r'<div[^>]+class=["\'][^"\']*\bcategory-list\b[^"\']*["\'][^>]+data-\s*category=["\']__ALL__["\'][^>]*>(.*?)</section>', page)
    if not block:
        block = page

    for section in re.findall(r"(?is)<section\b[^>]*>.*?</section>", block):
        dept = _first(r"<h4[^>]*>(.*?)</h4>", section)
        if not dept:
            continue
        for href in re.findall(r'(?is)<a\b[^>]+href=["\']([^"\']*/job/[^"\']+)["\']', section):
            url = urljoin(SITE, html.unescape(href))
            departments[url] = _strip_tags(dept)
    return departments


def _extract_description(page):
    h1 = re.search(r"(?is)<h1\b[^>]*>.*?</h1>", page)
    if not h1:
        return _strip_tags(page)

    start = h1.start()
    form = re.search(r"(?is)<form\b[^>]+name=['\"]apply['\"][^>]*>", page[h1.end():])
    end = h1.end() + form.start() if form else len(page)

    # The text content between the h1 and apply form is the full job body.
    fragment = page[start:end]
    return _strip_tags(fragment)


def _extract_location(page, description):
    loc_html = _first(r"(?is)<p[^>]*>\s*<strong[^>]*>\s*Location:\s*</strong>\s*(.*?)</p>", page)
    if loc_html:
        return _strip_tags(loc_html)
    m = re.search(r"(?im)^Location:\s*(.+)$", description)
    if m:
        return m.group(1).strip()
    return None


def crawl(ctx):
    departments = _homepage_departments(ctx)
    sitemap_xml = ctx.fetch(urljoin(SITE, "sitemap.xml"))
    urls = []
    seen = set()
    for loc in re.findall(r"(?is)<loc>\s*(.*?)\s*</loc>", sitemap_xml):
        url = html.unescape(loc).strip()
        if "/job/" not in url or url in seen:
            continue
        seen.add(url)
        urls.append(url)

    for url in urls:
        page = ctx.fetch(url)
        title = _first(r"(?is)<h1[^>]*>(.*?)</h1>", page)
        if not title:
            title = _first(r"(?is)<title[^>]*>\s*Careers at Tetrate:\s*(.*?)\s*</title>", page)
        title = _strip_tags(title or "")

        description = _extract_description(page)
        location = _extract_location(page, description) or "Remote"

        job = {
            "url": url,
            "title": title,
            "company": "Tetrate",
            "location": location,
            "description": description,
            "source": SITE,
            "apply_url": url,
        }

        dept = departments.get(url)
        if dept:
            job["department"] = dept

        external_id = _first(r'(?is)<input[^>]+name=["\']job-id["\'][^>]+value=["\']([^"\']+)["\']', page)
        if external_id:
            job["external_id"] = external_id

        yield job
