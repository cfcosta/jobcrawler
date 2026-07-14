
import html
import re
import xml.etree.ElementTree as ET

SITE = "https://intigriti.jobs.personio.com/xml"
EXTRA_HOSTS = []


class _TextExtractor:
    BLOCK_TAGS = (
        "address", "article", "aside", "blockquote", "br", "div", "dl", "dt",
        "dd", "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5",
        "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section",
        "table", "tr", "ul",
    )

    @classmethod
    def clean(cls, value):
        value = html.unescape(value or "")
        value = re.sub(
            r"<\s*(?:" + "|".join(cls.BLOCK_TAGS) + r")\b[^>]*>",
            "\n",
            value,
            flags=re.I,
        )
        value = re.sub(r"<\s*/\s*(?:" + "|".join(cls.BLOCK_TAGS) + r")\s*>", "\n", value, flags=re.I)
        value = re.sub(r"<[^>]+>", " ", value)
        value = html.unescape(value)
        value = value.replace("\xa0", " ")
        value = re.sub(r"[ \t\r\f\v]+", " ", value)
        value = re.sub(r" *\n *", "\n", value)
        return re.sub(r"\n{3,}", "\n\n", value).strip()


def _value(element, name):
    node = element.find(name)
    if node is None or node.text is None:
        return None
    value = node.text.strip()
    return value or None


def crawl(ctx):
    xml_text = ctx.fetch(SITE)
    root = ET.fromstring(xml_text)

    for position in root.findall("./position"):
        external_id = _value(position, "id")
        title = _value(position, "name")
        location = _value(position, "office")

        locations = []
        if location:
            locations.append(location)
        for office in position.findall("./additionalOffices/office"):
            if office.text and office.text.strip():
                additional = office.text.strip()
                if additional not in locations:
                    locations.append(additional)

        sections = []
        for section in position.findall("./jobDescriptions/jobDescription"):
            heading = _value(section, "name")
            body = _TextExtractor.clean(_value(section, "value"))
            if not body:
                continue
            sections.append((heading + "\n" + body) if heading else body)

        fields = {
            "url": "https://intigriti.jobs.personio.com/job/" + external_id,
            "title": title,
            "company": _value(position, "subcompany") or "Intigriti",
            "location": location,
            "description": "\n\n".join(sections),
            "external_id": external_id,
            "department": _value(position, "department"),
            "employment_type": _value(position, "employmentType"),
            "posted_at": _value(position, "createdAt"),
        }
        if len(locations) > 1:
            fields["locations"] = locations

        fields["source"] = SITE
        yield {key: value for key, value in fields.items() if value is not None}
