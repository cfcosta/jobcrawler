import datetime
import html
import re
import xml.etree.ElementTree as ET

SITE = "https://careers.sekoia.com/jobs.rss"
EXTRA_HOSTS = []

TT = "https://teamtailor.com/locations"


def _plain_text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|figcaption|figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|tr|ul)[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    lines = []
    for line in value.splitlines():
        cleaned = " ".join(line.split())
        if cleaned and (not lines or cleaned != lines[-1]):
            lines.append(cleaned)
    return "\n".join(lines)


def _text(element, name):
    child = element.find(name)
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def _parse_pub_date(value):
    match = re.match(
        r"^[A-Za-z]{3}, (\d{2}) ([A-Za-z]{3}) (\d{4}) "
        r"(\d{2}):(\d{2}):(\d{2}) ([+-])(\d{2})(\d{2})$",
        value,
    )
    if not match:
        return None

    months = {
        "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4,
        "May": 5, "Jun": 6, "Jul": 7, "Aug": 8,
        "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
    }
    month = months.get(match.group(2))
    if month is None:
        return None

    offset_minutes = int(match.group(8)) * 60 + int(match.group(9))
    if match.group(7) == "-":
        offset_minutes = -offset_minutes
    timezone = datetime.timezone(datetime.timedelta(minutes=offset_minutes))

    return datetime.datetime(
        int(match.group(3)),
        month,
        int(match.group(1)),
        int(match.group(4)),
        int(match.group(5)),
        int(match.group(6)),
        tzinfo=timezone,
    ).isoformat()


def crawl(ctx):
    response_text = ctx.fetch(SITE)
    root = ET.fromstring(response_text)

    workplace_types = {
        "fully": "remote",
        "hybrid": "hybrid",
        "none": "on-site",
    }

    for item in root.findall("./channel/item"):
        title = _text(item, "title")
        url = _text(item, "link")
        description = _plain_text(_text(item, "description"))

        location_names = []
        locations_node = item.find("{%s}locations" % TT)
        if locations_node is not None:
            for location_node in locations_node.findall("{%s}location" % TT):
                name = _text(location_node, "{%s}name" % TT)
                if name:
                    location_names.append(name)

        data = {
            "url": url,
            "title": title,
            "company": "Sekoia",
            "location": location_names[0] if location_names else None,
            "description": description,
            "source": SITE,
            "apply_url": url,
        }

        if location_names:
            data["locations"] = location_names

        department = _text(item, "{%s}department" % TT)
        if department:
            data["department"] = department

        external_id = _text(item, "guid")
        if external_id:
            data["external_id"] = external_id

        remote_status = _text(item, "remoteStatus")
        if remote_status in workplace_types:
            data["workplace_type"] = workplace_types[remote_status]

        pub_date = _text(item, "pubDate")
        if pub_date:
            posted_at = _parse_pub_date(pub_date)
            if posted_at:
                data["posted_at"] = posted_at

        yield data
