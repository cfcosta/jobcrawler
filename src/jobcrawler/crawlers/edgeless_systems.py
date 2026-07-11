
import html
import re
import xml.etree.ElementTree as ET

SITE = "https://edgeless-systems.jobs.personio.com/xml"
EXTRA_HOSTS = []

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")


def _text(node, name):
    child = node.find(name)
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def _clean_html(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6])\s*>", "\n", value)
    value = _TAG_RE.sub(" ", value)
    value = html.unescape(value)
    value = _WS_RE.sub(" ", value)
    value = re.sub(r"\n\s+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _fetch_text(ctx, url):
    response = ctx.fetch(url)
    if hasattr(response, "text"):
        return response.text
    return response


def crawl(ctx):
    xml_text = _fetch_text(ctx, SITE)
    root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)

    for position in root.findall(".//position"):
        external_id = _text(position, "id")
        title = _text(position, "name")
        primary_location = _text(position, "office")

        locations = []
        if primary_location:
            locations.append(primary_location)
        for office in position.findall("./additionalOffices/office"):
            if office.text and office.text.strip():
                locations.append(office.text.strip())

        sections = []
        for desc in position.findall("./jobDescriptions/jobDescription"):
            heading = _text(desc, "name")
            body = _clean_html(_text(desc, "value"))
            if heading and body:
                sections.append(heading + "\n" + body)
            elif body:
                sections.append(body)
            elif heading:
                sections.append(heading)

        description = "\n\n".join(section for section in sections if section).strip()
        if not (external_id and title and primary_location and description):
            continue

        job = {
            "url": "https://edgeless-systems.jobs.personio.com/job/%s" % external_id,
            "title": title,
            "company": "Edgeless Systems",
            "location": primary_location,
            "locations": locations or None,
            "description": description,
            "source": SITE,
            "external_id": external_id,
        }

        department = _text(position, "department")
        employment_type = _text(position, "employmentType")
        schedule = _text(position, "schedule")
        posted_at = _text(position, "createdAt")

        if department:
            job["department"] = department
        if employment_type:
            job["employment_type"] = employment_type
        if schedule:
            job["workplace_type"] = schedule
        if posted_at:
            job["posted_at"] = posted_at

        yield job
