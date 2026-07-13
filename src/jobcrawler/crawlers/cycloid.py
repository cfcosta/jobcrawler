import re
import xml.etree.ElementTree as ET

SITE = "https://cycloid.jobs.personio.de/xml"
EXTRA_HOSTS = []


def _clean_html(value):
    value = value or ""
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|div|dl|dt|dd|figcaption|"
        r"figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|"
        r"tr|ul)(?:\s[^>]*)?>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]*>", "", value)
    entities = {
        "&amp;": "&",
        "&lt;": "<",
        "&gt;": ">",
        "&quot;": '"',
        "&#39;": "'",
        "&apos;": "'",
        "&nbsp;": " ",
    }
    for encoded, decoded in entities.items():
        value = value.replace(encoded, decoded)

    def _decimal(match):
        try:
            return chr(int(match.group(1)))
        except (ValueError, OverflowError):
            return match.group(0)

    def _hexadecimal(match):
        try:
            return chr(int(match.group(1), 16))
        except (ValueError, OverflowError):
            return match.group(0)

    value = re.sub(r"&#([0-9]+);", _decimal, value)
    value = re.sub(r"&#[xX]([0-9a-fA-F]+);", _hexadecimal, value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _optional_text(position, tag):
    value = position.findtext(tag)
    if value is None:
        return None
    value = value.strip()
    return value or None


def crawl(ctx):
    response = ctx.fetch(SITE)
    root = ET.fromstring(response)

    for position in root.findall("./position"):
        external_id = _optional_text(position, "id")
        title = _optional_text(position, "name")
        location = _optional_text(position, "office")

        locations = []
        if location:
            locations.append(location)
        for office in position.findall("./additionalOffices/office"):
            if office.text and office.text.strip():
                locations.append(office.text.strip())

        sections = []
        for section in position.findall("./jobDescriptions/jobDescription"):
            heading = _optional_text(section, "name")
            body = _clean_html(section.findtext("value"))
            if heading and body:
                sections.append(heading + "\n\n" + body)
            elif body:
                sections.append(body)
        description = "\n\n".join(sections).strip()

        url = "https://cycloid.jobs.personio.de/job/" + external_id
        yield dict(
            url=url,
            source=SITE,
            title=title,
            company="Cycloid",
            location=location,
            locations=locations,
            description=description,
            employment_type=_optional_text(position, "employmentType"),
            department=_optional_text(position, "department"),
            posted_at=_optional_text(position, "createdAt"),
            external_id=external_id,
            apply_url=url,
        )
