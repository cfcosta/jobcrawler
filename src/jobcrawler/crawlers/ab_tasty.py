
from html import unescape
import re
import xml.etree.ElementTree as ET

SITE = "https://careers.abtasty.com/jobs.rss"
EXTRA_HOSTS = []

_TT = "{https://teamtailor.com/locations}"
_BLOCK_TAGS = (
    "address|article|aside|blockquote|br|div|dl|dt|dd|figcaption|figure|"
    "footer|h1|h2|h3|h4|h5|h6|header|hr|li|main|ol|p|pre|section|"
    "table|tr|ul"
)


def _html_to_text(value):
    # ElementTree decodes the outer XML entity layer. Decode Teamtailor's
    # remaining HTML entities before removing the HTML markup.
    value = unescape(value or "")
    value = re.sub(
        r"(?is)<\s*/?\s*(?:" + _BLOCK_TAGS + r")\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?is)<[^>]+>", "", value)
    value = unescape(value)

    lines = []
    for line in value.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def _value(node, path):
    value = node.findtext(path)
    if value is None:
        return None
    value = value.strip()
    return value or None


def crawl(ctx):
    response = ctx.fetch(SITE)
    xml_text = response.text if hasattr(response, "text") else response
    root = ET.fromstring(xml_text)

    for item in root.findall("./channel/item"):
        title = _value(item, "title")
        url = _value(item, "link")
        description = _html_to_text(item.findtext("description") or "")
        if not title or not url or not description:
            continue

        locations = []
        location_path = _TT + "locations/" + _TT + "location"
        for location_node in item.findall(location_path):
            name = _value(location_node, _TT + "name")
            if name:
                locations.append(name)

        kwargs = {
            "url": url,
            "title": title,
            "company": "AB Tasty",
            "description": description,
            "source": SITE,
            "external_id": _value(item, "guid"),
            "workplace_type": _value(item, "remoteStatus"),
            "department": _value(item, _TT + "department"),
        }
        if locations:
            kwargs["location"] = locations[0]
            kwargs["locations"] = locations
        else:
            # Some Teamtailor items omit tt:locations while stating the
            # location in the full posting's DETAILS section.
            match = re.search(
                r"(?im)^Location:\s*([^\n]+)",
                description,
            )
            if match:
                location = match.group(1).strip()
                if location:
                    kwargs["location"] = location

        yield {
            key: value for key, value in kwargs.items() if value is not None
        }
