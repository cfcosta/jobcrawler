"""Crawler for Spendesk's Teamtailor job board (career.spendesk.com RSS feed)."""

import html
import re
import xml.etree.ElementTree as ET

SITE = "https://career.spendesk.com/jobs.rss"
EXTRA_HOSTS = []
COMPANY = "Spendesk"

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6])\s*>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    text = "\n".join(part.strip() for part in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", text).strip() or None


def _text(node, tag):
    if node is None:
        return None
    child = node.find(tag)
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def _first_location(item):
    loc = item.find(".//{*}location")
    if loc is None:
        return None
    city = _text(loc, "{*}city")
    country = _text(loc, "{*}country")
    parts = [p for p in (city, country) if p]
    return ", ".join(parts) if parts else None


def _workplace_type(item):
    status = _text(item, "remoteStatus")
    if not status:
        return None
    lowered = status.lower()
    if "fully" in lowered or lowered == "remote":
        return "Remote"
    if "hybrid" in lowered or "temporary" in lowered or "partial" in lowered:
        return "Hybrid"
    if "none" in lowered or lowered == "no":
        return "On-site"
    return None


def crawl(ctx):
    root = ET.fromstring(ctx.fetch(SITE))
    for item in root.findall(".//item"):
        title = _text(item, "title")
        url = _text(item, "link")
        description = _strip_html(_text(item, "description"))
        location = _first_location(item)
        workplace = _workplace_type(item)
        if not location and workplace == "Remote":
            location = "Remote"
        if not (title and url and description and location):
            continue

        job = {
            "url": url,
            "title": title,
            "company": COMPANY,
            "location": location,
            "description": description,
            "source": SITE,
        }
        guid = _text(item, "guid")
        if guid:
            job["external_id"] = guid
        posted = _text(item, "pubDate")
        if posted:
            job["posted_at"] = posted
        department = _text(item, ".//{*}department")
        if department:
            job["department"] = department
        if workplace:
            job["workplace_type"] = workplace
        yield job
