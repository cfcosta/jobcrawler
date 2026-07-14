
import html
import re
import xml.etree.ElementTree as ET


SITE = "https://personio.jobs.personio.com/xml"
EXTRA_HOSTS = []


def _plain_text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/ul|/ol|/h[1-6]|/section|/article)\s*/?\s*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def crawl(ctx):
    response = ctx.fetch(SITE + "?language=en")
    root = ET.fromstring(response)

    for position in root.findall("./position"):
        external_id = (position.findtext("id") or "").strip()
        title = (position.findtext("name") or "").strip()
        primary_location = (position.findtext("office") or "").strip()

        locations = []
        if primary_location:
            locations.append(primary_location)
        for node in position.findall("./additionalOffices/office"):
            location = (node.text or "").strip()
            if location and location not in locations:
                locations.append(location)

        sections = []
        for section in position.findall("./jobDescriptions/jobDescription"):
            heading = (section.findtext("name") or "").strip()
            body = _plain_text(section.findtext("value"))
            if heading and body:
                sections.append(heading + "\n\n" + body)
            elif body:
                sections.append(body)
        description = "\n\n".join(sections).strip()

        company = (position.findtext("subcompany") or "").strip()
        department = (position.findtext("department") or "").strip()
        employment_type = (position.findtext("employmentType") or "").strip()
        posted_at = (position.findtext("createdAt") or "").strip()

        yield {
            "url": "https://personio.jobs.personio.com/job/" + external_id,
            "title": title,
            "company": company,
            "location": primary_location,
            "locations": locations,
            "description": description,
            "source": SITE,
            "department": department or None,
            "employment_type": employment_type or None,
            "posted_at": posted_at or None,
            "external_id": external_id or None,
        }
