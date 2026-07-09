import re, html
from xml.etree import ElementTree as ET
from datetime import datetime

SITE = "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss"
EXTRA_HOSTS = []


def _strip_html(text):
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|li|h[1-6]|tr|ul|ol)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _parse_date(date_str):
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
    raw = ctx.fetch(SITE)
    root = ET.fromstring(raw)
    channel = root.find("channel")
    if channel is None:
        return
    items = channel.findall("item")
    seen_links = set()
    for item in items:
        link = item.findtext("link", "").strip()
        if not link or link in seen_links:
            continue
        seen_links.add(link)

        title_raw = item.findtext("title", "").strip()
        if ": " in title_raw:
            company, role = title_raw.split(": ", 1)
            company = company.strip()
            role = role.strip()
        else:
            company = ""
            role = title_raw

        desc_html = item.findtext("description", "")
        description = _strip_html(desc_html)

        region = item.findtext("region", "").strip() or None
        job_type = item.findtext("type", "").strip() or None
        category = item.findtext("category", "").strip() or None

        posted_at = _parse_date(item.findtext("pubDate", ""))
        expires_at = _parse_date(item.findtext("expires_at", ""))

        guid = item.findtext("guid", "").strip() or None

        job = {
            "url": link,
            "title": role,
            "company": company,
            "location": region,
            "description": description,
            "source": SITE,
            "posted_at": posted_at,
            "expires_at": expires_at,
            "employment_type": job_type,
            "department": category,
            "external_id": guid,
        }
        apply = _apply_url(ctx, link)
        if apply:
            job["apply_url"] = apply
        yield job
