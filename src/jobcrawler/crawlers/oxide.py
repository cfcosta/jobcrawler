
import re
import html
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

SITE = "https://oxide.computer/careers/feed"
EXTRA_HOSTS = []


def _html_to_text(raw_html):
    text = raw_html or ""
    text = re.sub(r"(?is)<(script|style|svg)\b.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", " ", text)
    text = re.sub(r"(?i)</?(p|div|li|ul|ol|h1|h2|h3|h4|section|article|blockquote)\b[^>]*>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _first_match(pattern, text):
    match = re.search(pattern, text, flags=re.I)
    return match.group(1).strip() if match else None


def _location_from_text(description):
    located = _first_match(r"candidates located in ([^.]+)", description)
    if located:
        return located
    if re.search(r"\bBay Area\b", description, flags=re.I):
        return "Bay Area"
    if re.search(r"\bUnited States\b|\bU\.S\.\b|\bUS\b", description, flags=re.I):
        return "United States"
    # The feed has no structured location field; operator context states the board is remote-first US.
    return "Remote-first US"


def _entry_url(entry, ns):
    link = entry.find("a:link", ns)
    if link is not None and link.get("href"):
        return link.get("href")
    return (entry.findtext("a:id", default="", namespaces=ns) or "").strip()


def crawl(ctx):
    xml_text = ctx.fetch(SITE)
    root = ET.fromstring(xml_text)
    ns = {"a": "http://www.w3.org/2005/Atom"}

    for entry in root.findall("a:entry", ns):
        title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
        url = _entry_url(entry, ns)
        content_html = entry.findtext("a:content", default="", namespaces=ns) or ""
        description = _html_to_text(content_html)
        updated = (entry.findtext("a:updated", default="", namespaces=ns) or "").strip() or None

        if not title or not url or not description:
            continue

        apply_url = None
        apply_match = re.search(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>\s*Apply here\s*</a>', content_html, flags=re.I)
        if apply_match:
            apply_url = html.unescape(apply_match.group(1)).strip() or None

        path = urlparse(url).path.rstrip("/")
        external_id = path.rsplit("/", 1)[-1] if path else None

        job = {
            "url": url,
            "title": title,
            "company": "Oxide Computer Company",
            "location": _location_from_text(description),
            "description": description,
            "source": SITE,
        }
        if updated:
            job["posted_at"] = updated
        if external_id:
            job["external_id"] = external_id
        if apply_url:
            job["apply_url"] = apply_url

        yield job
