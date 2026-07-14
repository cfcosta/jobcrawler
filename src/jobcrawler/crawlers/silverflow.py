
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET

SITE = "https://silverflow.jobs.personio.com/xml"
EXTRA_HOSTS = []


def _html_to_text(value):
    value = re.sub(
        r"(?i)<li(?:\s[^>]*)?>",
        "\n- ",
        value,
    )
    value = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|"
        r"figcaption|figure|footer|h[1-6]|header|hr|main|nav|ol|p|pre|"
        r"section|table|tr|ul)(?:\s[^>]*)?>",
        "\n",
        value,
    )
    value = re.sub(r"(?s)<[^>]+>", "", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        clean = " ".join(line.split())
        if clean:
            lines.append(clean)
    return "\n".join(lines)


def _text(node, name):
    value = node.findtext(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def crawl(ctx):
    response = ctx.fetch(SITE)
    root = ET.fromstring(response)

    for position in root.findall("./position"):
        job_id = _text(position, "id")
        title = _text(position, "name")
        location = _text(position, "office")
        if not job_id or not title or not location:
            continue

        sections = []
        for section in position.findall("./jobDescriptions/jobDescription"):
            heading = _text(section, "name")
            body = _html_to_text(section.findtext("value") or "")
            if heading and body:
                sections.append(heading + "\n" + body)
            elif body:
                sections.append(body)

        description = "\n\n".join(sections)
        if not description:
            continue

        fields = {
            "url": "https://silverflow.jobs.personio.com/job/" + job_id,
            "source": SITE,
            "title": title,
            "company": "Silverflow",
            "location": location,
            "description": description,
            "external_id": job_id,
            "department": _text(position, "department"),
            "employment_type": _text(position, "employmentType"),
            "posted_at": _text(position, "createdAt"),
        }
        yield {key: value for key, value in fields.items() if value is not None}
