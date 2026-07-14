
import html
import re
import xml.etree.ElementTree as ET

SITE = "https://gnosis.jobs.personio.com/xml"
EXTRA_HOSTS = []


def _html_to_text(value):
    # Preserve common block and list boundaries before removing markup.
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value or "")
    value = re.sub(r"(?i)<\s*li(?:\s[^>]*)?>", "\n- ", value)
    value = re.sub(
        r"(?i)</?\s*(?:p|div|section|article|h[1-6]|ul|ol|table|tr|"
        r"blockquote|pre)(?:\s[^>]*)?>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _value(element, path):
    value = element.findtext(path)
    if value is None:
        return None
    value = value.strip()
    return value or None


def crawl(ctx):
    root = ET.fromstring(ctx.fetch(SITE))

    for position in root.findall("./position"):
        external_id = _value(position, "id")
        title = _value(position, "name")
        primary_location = _value(position, "office")
        if not external_id or not title or not primary_location:
            continue

        sections = []
        for section in position.findall("./jobDescriptions/jobDescription"):
            heading = _value(section, "name")
            body = _html_to_text(section.findtext("value") or "")
            if heading and body:
                sections.append(heading + "\n\n" + body)
            elif body:
                sections.append(body)

        description = "\n\n".join(sections).strip()
        if not description:
            continue

        locations = [primary_location]
        for office in position.findall("./additionalOffices/office"):
            if office.text and office.text.strip():
                locations.append(office.text.strip())

        url = "https://gnosis.jobs.personio.com/job/" + external_id
        job = {
            "url": url,
            "title": title,
            "company": _value(position, "subcompany") or "Gnosis",
            "location": primary_location,
            "locations": locations,
            "description": description,
            "source": SITE,
            "external_id": external_id,
            "apply_url": url,
        }

        department = _value(position, "department")
        employment_type = _value(position, "employmentType")
        posted_at = _value(position, "createdAt")
        if department is not None:
            job["department"] = department
        if employment_type is not None:
            job["employment_type"] = employment_type
        if posted_at is not None:
            job["posted_at"] = posted_at

        yield job
