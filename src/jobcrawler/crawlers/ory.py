
import html
import json
import re
import xml.etree.ElementTree as ET


SITE = "https://ory.jobs.personio.de/xml"
EXTRA_HOSTS = []


def _strip_html(value):
    if not value:
        return None
    value = re.sub(
        r"(?i)<\s*(br|/p|/div|/h[1-6]|/li|/ol|/ul|/section|/tr)\s*/?\s*>",
        "\n",
        value,
    )
    value = re.sub(r"(?i)<\s*li(?:\s[^>]*)?>", "\n- ", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    lines = []
    for line in value.splitlines():
        line = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines) or None


def _find_posting(value):
    if isinstance(value, dict):
        posting_type = value.get("@type")
        if posting_type == "JobPosting":
            return value
        if isinstance(posting_type, list) and "JobPosting" in posting_type:
            return value
        graph = value.get("@graph")
        if graph is not None:
            found = _find_posting(graph)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_posting(item)
            if found:
                return found
    return None


def _detail_description(ctx, url):
    page = ctx.fetch(url)
    scripts = re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        page,
        flags=re.I | re.S,
    )
    for script in scripts:
        try:
            posting = _find_posting(json.loads(script))
        except (TypeError, ValueError):
            posting = None
        if posting:
            description = _strip_html(posting.get("description"))
            if description:
                return description
    return None


def _feed_description(position):
    container = position.find("jobDescriptions")
    if container is None:
        return None

    sections = []
    for section in container.findall("jobDescription"):
        heading = _value(section, "name")
        body = _strip_html(section.findtext("value"))
        if heading and body:
            sections.append(heading + "\n" + body)
        elif body:
            sections.append(body)
    return "\n\n".join(sections) or None


def _value(element, tag):
    value = element.findtext(tag)
    if value is None:
        return None
    value = value.strip()
    return value or None


def crawl(ctx):
    root = ET.fromstring(ctx.fetch(SITE))

    for position in root.findall("./position"):
        external_id = _value(position, "id")
        title = _value(position, "name")
        if not external_id or not title:
            continue

        url = "https://ory.jobs.personio.de/job/" + external_id
        description = _feed_description(position)
        if not description:
            description = _detail_description(ctx, url)
        if not description:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": _value(position, "subcompany") or "Ory Corp",
            "location": _value(position, "office"),
            "description": description,
            "external_id": external_id,
            "department": _value(position, "department"),
            "employment_type": _value(position, "employmentType"),
            "posted_at": _value(position, "createdAt"),
            "source": SITE,
        }
        yield {key: value for key, value in fields.items() if value is not None}
