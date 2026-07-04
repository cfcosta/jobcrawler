SITE = "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss"
EXTRA_HOSTS = []

import xml.etree.ElementTree as ET
import html
import re
from datetime import datetime


def _strip_tags(text):
    """Unescape HTML entities and strip tags."""
    unescaped = html.unescape(text)
    # Replace <br>, <p>, </p>, </li>, </h1-6> with newlines for readability
    unescaped = re.sub(r"(?i)<br\s*/?>", "\n", unescaped)
    unescaped = re.sub(r"(?i)</(p|div|li|h[1-6])>", "\n", unescaped)
    unescaped = re.sub(r"(?i)<li[^>]*>", "- ", unescaped)
    clean = re.sub(r"<[^>]+>", "", unescaped)
    # Collapse excessive whitespace
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    clean = re.sub(r"[ \t]+", " ", clean)
    return clean.strip()


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
    rss_text = ctx.fetch(SITE)
    root = ET.fromstring(rss_text)
    channel = root.find("channel")
    items = channel.findall("item")

    for item in items:
        # Extract fields
        title_raw = ""
        link = ""
        description_raw = ""
        pub_date = ""
        expires_at = ""
        region = ""
        job_type = ""
        category = ""
        guid = ""

        for child in item:
            tag = child.tag.split("}")[-1]
            text = (child.text or "").strip()
            if tag == "title":
                title_raw = text
            elif tag == "link":
                link = text
            elif tag == "description":
                description_raw = text
            elif tag == "pubDate":
                pub_date = text
            elif tag == "expires_at":
                expires_at = text
            elif tag == "region":
                region = text
            elif tag == "type":
                job_type = text
            elif tag == "category":
                category = text
            elif tag == "guid":
                guid = text

        # Split "Company: Role"
        if ": " in title_raw:
            company, title = title_raw.split(": ", 1)
        else:
            company = ""
            title = title_raw

        # Full description
        description = _strip_tags(description_raw)

        job = {
            "url": link,
            "title": title,
            "company": company,
            "location": region if region else "Remote",
            "description": description,
            "source": SITE,
        }

        if job_type:
            job["employment_type"] = job_type
        if category:
            job["department"] = category
        if pub_date:
            parsed = _parse_date(pub_date)
            if parsed:
                job["posted_at"] = parsed
        if expires_at:
            parsed = _parse_date(expires_at)
            if parsed:
                job["expires_at"] = parsed
        if guid:
            job["external_id"] = guid

        yield job
