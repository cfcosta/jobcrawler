
import html
import re
import xml.etree.ElementTree as ET


SITE = "https://qamine-portugal.jobs.personio.com/xml"
EXTRA_HOSTS = []


def _html_to_text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)<\s*(?:br|p|div|li|ul|ol|h[1-6]|section|article|blockquote|tr)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(
        r"(?i)</\s*(?:p|div|li|ul|ol|h[1-6]|section|article|blockquote|tr)\s*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _value(element, tag):
    value = element.findtext(tag)
    if value is None:
        return None
    value = value.strip()
    return value or None


def crawl(ctx):
    response = ctx.fetch(SITE)
    body = response.text if hasattr(response, "text") else response
    if isinstance(body, bytes):
        body = body.decode("utf-8")

    root = ET.fromstring(body)
    for position in root.findall("position"):
        external_id = _value(position, "id")
        title = _value(position, "name")
        primary_location = _value(position, "office")

        locations = []
        if primary_location:
            locations.append(primary_location)
        for office in position.findall("./additionalOffices/office"):
            if office.text and office.text.strip():
                locations.append(office.text.strip())

        sections = []
        for section in position.findall("./jobDescriptions/jobDescription"):
            heading = _value(section, "name")
            section_text = _html_to_text(section.findtext("value"))
            if not section_text:
                continue
            if heading:
                sections.append(f"{heading}\n{section_text}")
            else:
                sections.append(section_text)

        fields = {
            "url": f"https://qamine-portugal.jobs.personio.com/job/{external_id}",
            "title": title,
            "company": "Qamine, Lda",
            "location": primary_location,
            "locations": locations or None,
            "description": "\n\n".join(sections),
            "external_id": external_id,
            "department": _value(position, "department"),
            "employment_type": _value(position, "employmentType"),
            "posted_at": _value(position, "createdAt"),
            "source": SITE,
        }
        yield {key: value for key, value in fields.items() if value is not None}
