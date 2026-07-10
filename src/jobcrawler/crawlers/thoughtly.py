
import re
import html
import xml.etree.ElementTree as ET

SITE = "https://feed.homerun.co/thoughtly"
EXTRA_HOSTS = []


def _strip_html(value):
    text = html.unescape(value or "")
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-4]|tr)\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*(p|div|li|h[1-4]|tr)[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _text(node, path, ns):
    found = node.find(path, ns)
    if found is not None and found.text:
        value = found.text.strip()
        return value or None
    return None


def _alternate_url(entry, ns):
    fallback = None
    for link in entry.findall("a:link", ns):
        href = link.attrib.get("href")
        if not href:
            continue
        if link.attrib.get("rel") == "alternate":
            return href
        if fallback is None:
            fallback = href
    return fallback


def _salary_text(description):
    match = re.search(
        r"(?:Our compensation range for this role is|The compensation range for this role is)[^.]*\.",
        description,
        flags=re.IGNORECASE,
    )
    return match.group(0).strip() if match else None


def crawl(ctx):
    xml_text = ctx.fetch(SITE)
    root = ET.fromstring(xml_text)
    ns = {"a": "http://www.w3.org/2005/Atom"}

    for entry in root.findall("a:entry", ns):
        title = _text(entry, "a:title", ns)
        url = _alternate_url(entry, ns)
        raw_description = _text(entry, "a:description", ns)
        description = _strip_html(raw_description)
        location = _text(entry, "a:location/a:name", ns)
        external_id = _text(entry, "a:id", ns)

        if not (title and url and description and location):
            continue

        job = {
            "url": url,
            "title": title,
            "company": "Thoughtly",
            "location": location,
            "locations": [location],
            "description": description,
            "source": SITE,
        }

        if external_id:
            job["external_id"] = external_id

        department = _text(entry, "a:department/a:name", ns)
        if department:
            job["department"] = department

        employment_type = _text(entry, "a:type/a:name", ns)
        if employment_type:
            job["employment_type"] = employment_type

        salary = _salary_text(description)
        if salary:
            job["salary"] = salary

        yield job
