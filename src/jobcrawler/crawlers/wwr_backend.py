import re
import html
from xml.etree import ElementTree as ET
from datetime import datetime
from urllib.parse import urlparse

SITE = "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss"
EXTRA_HOSTS = []


def _strip_tags(text):
    """Remove HTML tags and decode entities."""
    if not text:
        return ""
    # Unescape HTML entities first
    text = html.unescape(text)
    # Remove tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _parse_date(date_str):
    """Parse RFC 822 date string to ISO format."""
    if not date_str:
        return None
    try:
        dt = datetime.strptime(date_str.strip(), "%a, %d %b %Y %H:%M:%S %z")
        return dt.isoformat()
    except Exception:
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
    """Crawl the WWR back-end programming RSS feed."""
    resp = ctx.fetch(SITE)
    root = ET.fromstring(resp)
    channel = root.find("channel")
    if channel is None:
        return
    for item in channel.findall("item"):
        title_raw = item.findtext("title") or ""
        # Title format: "Company: Role"
        if ": " in title_raw:
            company, job_title = title_raw.split(": ", 1)
        else:
            company = ""
            job_title = title_raw

        link = item.findtext("link") or ""
        guid = item.findtext("guid") or ""
        url = link or guid

        desc_html = item.findtext("description") or ""
        description = _strip_tags(desc_html)

        region = item.findtext("region") or ""
        location = region if region else None

        job_type = item.findtext("type") or ""
        employment_type = job_type if job_type else None

        category = item.findtext("category") or ""
        department = category if category else None

        pub_date = item.findtext("pubDate") or ""
        posted_at = _parse_date(pub_date)

        expires_at_raw = item.findtext("expires_at") or ""
        expires_at = _parse_date(expires_at_raw)

        skills = item.findtext("skills") or ""

        external_id = None
        if guid:
            external_id = guid.rsplit("/", 1)[-1] if "/" in guid else guid

        job = {
            "url": url,
            "title": job_title.strip(),
            "company": company.strip(),
            "location": location,
            "description": description,
            "employment_type": employment_type,
            "department": department,
            "posted_at": posted_at,
            "expires_at": expires_at,
            "external_id": external_id,
            "source": SITE,
        }
        apply = _apply_url(ctx, url)
        if apply:
            job["apply_url"] = apply
        yield job
