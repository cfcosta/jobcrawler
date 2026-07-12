from datetime import datetime, timezone
import html
import re
import xml.etree.ElementTree as ET

SITE = "https://bunnynet.teamtailor.com/jobs.rss"
EXTRA_HOSTS = []

_TT = "{https://teamtailor.com/locations}"


def _html_to_text(value):
    value = html.unescape(value)
    value = re.sub(r"(?is)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?is)<\s*li(?:\s[^>]*)?>", "\n- ", value)
    value = re.sub(
        r"(?is)</?\s*(?:address|article|aside|blockquote|div|footer|h[1-6]|"
        r"header|hr|main|ol|p|pre|section|table|tr|ul)(?:\s[^>]*)?>",
        "\n",
        value,
    )
    value = re.sub(r"(?is)<[^>]+>", "", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _text(node, name):
    value = node.findtext(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _posted_at(value):
    if not value:
        return None

    months = {
        "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4,
        "May": 5, "Jun": 6, "Jul": 7, "Aug": 8,
        "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
    }

    try:
        parts = value.split()
        day = int(parts[1])
        month = months[parts[2]]
        year = int(parts[3])
        hour, minute, second = (int(part) for part in parts[4].split(":"))
        offset = parts[5]
        offset_minutes = int(offset[1:3]) * 60 + int(offset[3:5])
        if offset[0] == "-":
            offset_minutes = -offset_minutes

        local = datetime(
            year, month, day, hour, minute, second, tzinfo=timezone.utc
        )
        return local.timestamp() - offset_minutes * 60
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def crawl(ctx):
    root = ET.fromstring(ctx.fetch(SITE))

    for item in root.findall("./channel/item"):
        title = _text(item, "title")
        url = _text(item, "link")
        description = _html_to_text(item.findtext("description") or "")

        location_names = []
        locations_node = item.find(_TT + "locations")
        if locations_node is not None:
            for location_node in locations_node.findall(_TT + "location"):
                name = _text(location_node, _TT + "name")
                if name:
                    location_names.append(name)

        if not title or not url or not description or not location_names:
            continue

        workplace_type = {
            "fully": "remote",
            "hybrid": "hybrid",
            "none": "on-site",
        }.get(_text(item, "remoteStatus"))

        yield {
            "url": url,
            "title": title,
            "company": "bunny.net",
            "location": location_names[0],
            "locations": location_names,
            "description": description,
            "source": SITE,
            "workplace_type": workplace_type,
            "department": _text(item, _TT + "department"),
            "posted_at": _posted_at(_text(item, "pubDate")),
            "external_id": _text(item, "guid"),
            "apply_url": url,
        }
