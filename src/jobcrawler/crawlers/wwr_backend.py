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

        # Extract apply_url from description if present
        apply_url = None
        url_match = re.search(r'href="(https?://[^"]+)"', html.unescape(desc_html))
        if url_match:
            candidate = url_match.group(1)
            # Only use non-weworkremotely URLs as apply_url
            if "weworkremotely.com" not in candidate:
                apply_url = candidate

        external_id = None
        if guid:
            external_id = guid.rsplit("/", 1)[-1] if "/" in guid else guid

        yield {
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
            "apply_url": apply_url,
            "source": SITE,
        }
