import re
import html
import xml.etree.ElementTree as ET

SITE = "https://careers.payfit.com/jobs.rss"
EXTRA_HOSTS = []

_TT = "https://teamtailor.com/locations"

_MONTHS = {
    "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
    "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
    "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
}


def _posted_at(value):
    if not value:
        return None
    match = re.fullmatch(
        r"[A-Za-z]{3}, (\d{2}) ([A-Za-z]{3}) (\d{4}) "
        r"(\d{2}:\d{2}:\d{2}) ([+-])(\d{2})(\d{2})",
        value,
    )
    if not match:
        return None
    day, month, year, clock, sign, offset_hour, offset_minute = match.groups()
    month_number = _MONTHS.get(month)
    if not month_number:
        return None
    return (
        year + "-" + month_number + "-" + day + "T" + clock
        + sign + offset_hour + ":" + offset_minute
    )


def _value(node, name):
    value = node.findtext(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _plain_text(value):
    value = html.unescape(value or "")
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(
        r"(?i)</?(?:p|div|li|ul|ol|h[1-6]|section|article|blockquote|pre|tr|table)[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    lines = []
    for line in value.replace("\r", "\n").split("\n"):
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def crawl(ctx):
    root = ET.fromstring(ctx.fetch(SITE))
    seen = set()

    for item in root.findall("./channel/item"):
        title = _value(item, "title")
        url = _value(item, "link")
        description = _plain_text(item.findtext("description"))
        if not title or not url or not description or url in seen:
            continue
        seen.add(url)

        locations = []
        path = "{%s}locations/{%s}location" % (_TT, _TT)
        for location_node in item.findall(path):
            name = _value(location_node, "{%s}name" % _TT)
            if name and name not in locations:
                locations.append(name)

        fields = {
            "url": url,
            "title": title,
            "company": "PayFit",
            "location": locations[0] if locations else None,
            "locations": locations or None,
            "description": description,
            "source": SITE,
            "department": _value(item, "{%s}department" % _TT),
            "workplace_type": _value(item, "remoteStatus"),
            "posted_at": _posted_at(_value(item, "pubDate")),
            "external_id": _value(item, "guid"),
        }
        yield dict((key, value) for key, value in fields.items()
                   if value is not None)
