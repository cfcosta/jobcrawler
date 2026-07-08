
import re
import html
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

SITE = "https://jobs.rubyonrails.org/"
EXTRA_HOSTS = []


def _text_from_html(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|tr)\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _parse_date(value):
    if not value:
        return None
    value = str(value).strip()
    if re.match(r"\d{4}-\d{2}-\d{2}", value):
        return value[:10]
    match = re.search(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})", value)
    months = {
        "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
        "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
        "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
    }
    if match and match.group(2) in months:
        return "%s-%s-%02d" % (match.group(3), months[match.group(2)], int(match.group(1)))
    return None


def _company_from_title(title):
    if " at " in title:
        return title.rsplit(" at ", 1)[1].strip() or None
    return None


def _job_title_from_feed_title(title):
    if " at " in title:
        return title.rsplit(" at ", 1)[0].strip()
    return title.strip() if title else None


def _labeled_line(text, labels):
    if not text:
        return None
    for line in text.splitlines():
        clean = line.strip(" -:\t")
        for label in labels:
            pattern = r"(?i)^" + re.escape(label) + r"\s*:\s*(.+)$"
            match = re.match(pattern, clean)
            if match:
                value = match.group(1).strip(" -;\t")
                return value or None
    return None


def _location_from_description(text):
    value = _labeled_line(text, ["Location", "Work location", "Job Location"])
    if value:
        return value
    if re.search(r"(?i)\bremote\b", text or ""):
        return "Remote"
    return None


def _salary_from_description(text):
    value = _labeled_line(text, ["Compensation Range", "Compensation", "Salary", "Pay Range"])
    return value


def _employment_type(text):
    if re.search(r"(?i)\bfull[- ]time\b", text or ""):
        return "Full-time"
    if re.search(r"(?i)\bpart[- ]time\b", text or ""):
        return "Part-time"
    if re.search(r"(?i)\bcontract\b", text or ""):
        return "Contract"
    return None


def _workplace_type(text, location):
    haystack = " ".join([location or "", text or ""])
    if re.search(r"(?i)\bremote\b", haystack):
        return "remote"
    if re.search(r"(?i)\bhybrid\b", haystack):
        return "hybrid"
    if re.search(r"(?i)\bon[- ]site\b", haystack):
        return "on-site"
    return None


def _clean_job(data):
    return {k: v for k, v in data.items() if v not in (None, "")}


def crawl(ctx):
    rss = ctx.fetch(urljoin(SITE, "jobs.rss"))
    root = ET.fromstring(rss)
    for item in root.findall("./channel/item"):
        feed_title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or item.findtext("guid") or "").strip()
        description = _text_from_html(item.findtext("description") or "")
        if not url or not feed_title or not description:
            continue

        location = _location_from_description(description)

        yield _clean_job({
            "url": url,
            "title": _job_title_from_feed_title(feed_title),
            "company": _company_from_title(feed_title) or "Rails Job Board",
            "location": location or "Remote",
            "description": description,
            "source": SITE,
            "posted_at": _parse_date(item.findtext("pubDate")),
            "salary": _salary_from_description(description),
            "employment_type": _employment_type(description),
            "workplace_type": _workplace_type(description, location),
        })
