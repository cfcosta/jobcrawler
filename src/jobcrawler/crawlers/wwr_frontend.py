SITE = "https://weworkremotely.com/categories/remote-front-end-programming-jobs.rss"
EXTRA_HOSTS = []

import re, html
import xml.etree.ElementTree as ET
from datetime import datetime


def _strip_tags(html_str):
    """Remove HTML tags, unescape entities, and clean whitespace."""
    if not html_str:
        return ""
    text = html.unescape(html_str)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _parse_date(date_str):
    """Parse RSS date format like 'Mon, 22 Jun 2026 15:29:54 +0000'."""
    if not date_str:
        return None
    try:
        dt = datetime.strptime(date_str.strip(), "%a, %d %b %Y %H:%M:%S %z")
        return dt.isoformat()
    except Exception:
        return None


def _build_location(region, country, state):
    parts = []
    if state and state.strip():
        parts.append(state.strip())
    if region and region.strip():
        parts.append(region.strip())
    if country and country.strip():
        parts.append(country.strip())
    return ", ".join(parts) if parts else None


def _text_or_none(elem):
    if elem is not None and elem.text:
        return elem.text.strip()
    return None


def _apply_url(ctx, page_url):
    """Return the external application URL for a WWR listing, or None.

    The RSS feed never carries the real per-job apply link — only the WWR listing
    page and the company's homepage. The apply link is the "Apply now" button on the
    listing page itself: ``<a id="job-cta-alt" href="https://company-ats/...">``. So
    fetch the page and read that href. Returns None on any fetch/parse failure, when the
    button is absent, or when it points back at WWR (an in-house form, not a distinct
    target). The caller then omits ``apply_url`` entirely, so a transient miss never
    clobbers a value a previous crawl already stored.
    """
    if not page_url:
        return None
    try:
        page = ctx.fetch(page_url)
    except Exception:
        return None
    tag = re.search(r'<a\b[^>]*\bid=["\']job-cta-alt["\'][^>]*>', page, re.IGNORECASE)
    if not tag:
        return None
    href = re.search(r'\bhref=["\']([^"\']+)["\']', tag.group(0), re.IGNORECASE)
    if not href:
        return None
    url = html.unescape(href.group(1)).strip()
    if not url.lower().startswith(("http://", "https://")):
        return None
    if "weworkremotely.com" in url.lower():
        return None
    return url


def crawl(ctx):
    rss_text = ctx.fetch(SITE)
    root = ET.fromstring(rss_text)
    items = root.findall(".//item")

    for item in items:
        full_title = _text_or_none(item.find("title")) or ""
        if ": " in full_title:
            company, role = full_title.split(": ", 1)
        else:
            company, role = "", full_title

        url = _text_or_none(item.find("link")) or ""
        if not url:
            continue

        desc_html = _text_or_none(item.find("description")) or ""
        description = _strip_tags(desc_html)
        if not description:
            continue

        region = _text_or_none(item.find("region")) or ""
        country = _text_or_none(item.find("country")) or ""
        state = _text_or_none(item.find("state")) or ""
        location = _build_location(region, country, state)

        employment_type = _text_or_none(item.find("type"))
        posted_at = _parse_date(_text_or_none(item.find("pubDate")))
        expires_at = _parse_date(_text_or_none(item.find("expires_at")))

        guid = _text_or_none(item.find("guid"))

        job = {
            "url": url,
            "title": role.strip(),
            "company": company.strip(),
            "location": location,
            "description": description,
            "source": SITE,
        }
        if employment_type:
            job["employment_type"] = employment_type
        if posted_at:
            job["posted_at"] = posted_at
        if expires_at:
            job["expires_at"] = expires_at
        if guid:
            job["external_id"] = guid

        apply = _apply_url(ctx, url)
        if apply:
            job["apply_url"] = apply

        yield job
