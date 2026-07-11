
import re
import html

SITE = "https://www.remotejobs.com/jobs?q=software"
EXTRA_HOSTS = []

_MONTHS = {
    "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
    "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
    "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
}

def _text(fragment):
    if not fragment:
        return ""
    fragment = re.sub(r"^\s*<!\[CDATA\[|\]\]>\s*$", "", fragment, flags=re.S)
    fragment = re.sub(r"(?is)<(script|style|svg|noscript)\b.*?</\1>", " ", fragment)
    fragment = re.sub(r"(?i)<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"(?i)</?(p|div|li|ul|ol|h[1-6]|tr|table|section|article)\b[^>]*>", "\n", fragment)
    fragment = re.sub(r"(?s)<[^>]+>", " ", fragment)
    fragment = html.unescape(fragment)
    fragment = fragment.replace("\xa0", " ")
    fragment = re.sub(r"[ \t\r\f\v]+", " ", fragment)
    fragment = re.sub(r" *\n *", "\n", fragment)
    fragment = re.sub(r"\n{3,}", "\n\n", fragment)
    return fragment.strip()

def _tag(item, tag):
    m = re.search(r"<" + tag + r"\b[^>]*>(.*?)</" + tag + r">", item, re.S | re.I)
    return _text(m.group(1)) if m else None

def _parse_title(feed_title):
    title = feed_title or ""
    location = None
    company = None
    workplace_type = None

    parts = [p.strip() for p in title.rsplit(" - ", 2)]
    if len(parts) == 3:
        title, company, location = parts
    elif len(parts) == 2:
        title, company = parts

    if location and location.lower() == "remote":
        workplace_type = "Remote"

    return title.strip(), company, location, workplace_type

def _external_id(guid):
    if not guid:
        return None
    m = re.match(r"(\d+)", guid)
    return m.group(1) if m else guid

def _posted_at(pub_date):
    if not pub_date:
        return None
    m = re.match(r"(?:[A-Za-z]{3},\s*)?(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})\s+(\d{2}:\d{2}:\d{2})\s+([+-]\d{2})(\d{2})", pub_date)
    if not m:
        return None
    day, mon, year, clock, off_h, off_m = m.groups()
    month = _MONTHS.get(mon)
    if not month:
        return None
    return year + "-" + month + "-" + day.zfill(2) + "T" + clock + off_h + ":" + off_m

def crawl(ctx):
    rss = ctx.fetch("https://www.remotejobs.com/jobs.rss")
    for item in re.findall(r"<item\b.*?</item>", rss, re.S | re.I):
        feed_title = _tag(item, "title")
        url = _tag(item, "link")
        department = _tag(item, "category")
        description = _tag(item, "description")
        pub_date = _tag(item, "pubDate")
        guid = _tag(item, "guid")

        title, company, location, workplace_type = _parse_title(feed_title)
        if not location:
            location = workplace_type or "Remote"

        job = {
            "url": url,
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "source": SITE,
        }

        posted_at = _posted_at(pub_date)
        external_id = _external_id(guid)
        if department:
            job["department"] = department
        if posted_at:
            job["posted_at"] = posted_at
        if external_id:
            job["external_id"] = external_id
        if workplace_type:
            job["workplace_type"] = workplace_type

        if job.get("url") and job.get("title") and job.get("company") and job.get("location") and job.get("description"):
            yield job
