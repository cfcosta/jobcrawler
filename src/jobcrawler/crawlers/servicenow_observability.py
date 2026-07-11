
import html
import re
import xml.etree.ElementTree as ET

SITE = "https://careers.servicenow.com/jobs/?search=observability"
EXTRA_HOSTS = []


def _text(node, name):
    child = node.find(name)
    if child is None or child.text is None:
        return None
    value = html.unescape(child.text).strip()
    return value or None


def _clean_html(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|h[1-6]|li|ul|ol|tr|section|article)\s*>", "\n", value)
    value = re.sub(r"(?s)<script\b.*?</script>", " ", value)
    value = re.sub(r"(?s)<style\b.*?</style>", " ", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"\r", "\n", value)
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r"\n\s*\n\s*\n+", "\n\n", value)
    value = re.sub(r" *\n *", "\n", value)
    return value.strip() or None


def _location(parts):
    return ", ".join(part for part in parts if part)


def crawl(ctx):
    feed_url = "https://careers.servicenow.com/jobs/xml/?rss=true"
    xml_text = ctx.fetch(feed_url)
    root = ET.fromstring(xml_text)

    for node in root.findall(".//job"):
        title = _text(node, "title")
        url = _text(node, "url")
        description = _clean_html(_text(node, "description"))
        company = _text(node, "company") or "ServiceNow"

        city = _text(node, "city")
        state = _text(node, "state")
        country = _text(node, "country")
        location = _location([city, state, country]) or city or state or country

        if not (title and url and description and location):
            continue

        job = {
            "url": url,
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "source": "ServiceNow Careers",
        }

        external_id = _text(node, "requisitionid") or _text(node, "referencenumber") or _text(node, "apijobid")
        employment_type = _text(node, "jobtype")
        department = _text(node, "category")
        workplace_type = _text(node, "remotetype")

        if external_id:
            job["external_id"] = external_id
        if employment_type:
            job["employment_type"] = employment_type
        if department:
            job["department"] = department
        if workplace_type:
            job["workplace_type"] = workplace_type
        job["apply_url"] = url

        yield job
