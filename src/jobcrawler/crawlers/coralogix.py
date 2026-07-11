
import re
from html import unescape
from urllib.parse import urljoin

SITE = "https://coralogix.com/careers/"
EXTRA_HOSTS = []

COMPANY = "Coralogix"


def _html_text(fragment):
    text = unescape(fragment or "")
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*/\s*(p|div|section|article|li|ul|ol|h[1-6])\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li\b[^>]*>", "\n- ", text)
    text = re.sub(r"(?is)<\s*(script|style)\b.*?<\s*/\s*\1\s*>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _attr(attrs, name):
    m = re.search(r'\b%s=["\']([^"\']*)["\']' % re.escape(name), attrs or "", re.I)
    if m:
        return _html_text(m.group(1))
    return None


def _clean(fragment):
    return _html_text(fragment).strip()


def _fetch(ctx, url):
    try:
        return ctx.fetch(url)
    except TypeError:
        return ctx.fetch(url, "GET", "")


def _extract_listing(html):
    item_re = re.compile(
        r'<div\b(?=[^>]*\bclass=["\'][^"\']*\bcareer-item\b[^"\']*["\'])([^>]*)>(.*?)</div>',
        re.I | re.S,
    )
    seen = set()
    for attrs, body in item_re.findall(html or ""):
        href_m = re.search(r'<a\b[^>]*href=["\']([^"\']+)["\']', body, re.I | re.S)
        title_m = re.search(r'<strong\b[^>]*>(.*?)</strong>', body, re.I | re.S)
        loc_m = re.search(r'<small\b[^>]*>(.*?)</small>', body, re.I | re.S)
        if not href_m or not title_m:
            continue

        url = urljoin(SITE, unescape(href_m.group(1)).strip())
        if url in seen:
            continue
        seen.add(url)

        location = _clean(loc_m.group(1)) if loc_m else (_attr(attrs, "data-loc") or "")
        yield {
            "url": url,
            "title": _clean(title_m.group(1)),
            "location": location,
            "department": _attr(attrs, "data-dept"),
        }


def _class_block(html, class_name):
    pattern = (
        r'<(?P<tag>[a-z0-9]+)\b(?=[^>]*\bclass=["\'][^"\']*\b'
        + re.escape(class_name)
        + r'\b[^"\']*["\'])[^>]*>'
    )
    m = re.search(pattern, html or "", re.I | re.S)
    if not m:
        return ""
    tag = m.group("tag").lower()
    pos = m.end()
    depth = 1
    tag_re = re.compile(r'</?%s\b[^>]*>' % re.escape(tag), re.I | re.S)
    for tm in tag_re.finditer(html, pos):
        if tm.group(0).startswith("</"):
            depth -= 1
            if depth == 0:
                return html[m.end():tm.start()]
        else:
            depth += 1
    return html[m.end():]


def _extract_detail(html, fallback):
    section = _class_block(html, "single-job") or html

    title_m = re.search(r'<h2\b[^>]*class=["\'][^"\']*\bcomeet-position-name\b[^"\']*["\'][^>]*>(.*?)</h2>', section, re.I | re.S)
    loc_m = re.search(r'<span\b[^>]*class=["\'][^"\']*\bcomeet-position-location\b[^"\']*["\'][^>]*>(.*?)</span>', section, re.I | re.S)
    emp_m = re.search(r'<span\b[^>]*class=["\'][^"\']*\bcomeet-position-employmenttype\b[^"\']*["\'][^>]*>(.*?)</span>', section, re.I | re.S)

    parts = []
    pair_re = re.compile(
        r'<h4\b[^>]*class=["\'][^"\']*\bcomeet-position-(?:description|requirements)\b[^"\']*["\'][^>]*>(.*?)</h4>\s*'
        r'<div\b[^>]*class=["\'][^"\']*\bcomeet-user-text\b[^"\']*["\'][^>]*>(.*?)</div>',
        re.I | re.S,
    )
    for heading, body in pair_re.findall(section):
        heading_text = _clean(heading)
        body_text = _clean(body)
        if heading_text and body_text:
            parts.append(heading_text + "\n" + body_text)
        elif body_text:
            parts.append(body_text)

    if not parts:
        for body in re.findall(r'<div\b[^>]*class=["\'][^"\']*\bcomeet-user-text\b[^"\']*["\'][^>]*>(.*?)</div>', section, re.I | re.S):
            text = _clean(body)
            if text:
                parts.append(text)

    detail = {
        "title": _clean(title_m.group(1)) if title_m else fallback.get("title"),
        "location": _clean(loc_m.group(1)) if loc_m else fallback.get("location"),
        "description": "\n\n".join(parts).strip(),
    }

    employment_type = _clean(emp_m.group(1)).lstrip("·").strip() if emp_m else None
    if employment_type:
        detail["employment_type"] = employment_type

    uid_m = re.search(r'data-position-uid=["\']([^"\']+)["\']', section, re.I)
    if uid_m:
        detail["external_id"] = unescape(uid_m.group(1)).strip()

    return detail


def crawl(ctx):
    listing_html = _fetch(ctx, SITE)
    for item in _extract_listing(listing_html):
        detail_html = _fetch(ctx, item["url"])
        detail = _extract_detail(detail_html, item)

        job = {
            "url": item["url"],
            "title": detail.get("title") or item["title"],
            "company": COMPANY,
            "location": detail.get("location") or item["location"],
            "description": detail.get("description"),
            "source": SITE,
            "apply_url": item["url"],
        }

        if item.get("department"):
            job["department"] = item["department"]
        if item.get("location"):
            job["locations"] = [item["location"]]
        if detail.get("employment_type"):
            job["employment_type"] = detail["employment_type"]
        if detail.get("external_id"):
            job["external_id"] = detail["external_id"]

        yield job
