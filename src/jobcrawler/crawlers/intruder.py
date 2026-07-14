
import html
import re
import xml.etree.ElementTree as ET

SITE = "https://careers.intruder.io/jobs.rss"
EXTRA_HOSTS = []

TT = "https://teamtailor.com/locations"


def _text(node, name):
    child = node.find(name)
    if child is None:
        return None
    value = (child.text or "").strip()
    return value or None


def _html_to_text(value):
    value = html.unescape(value or "")
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(
        r"(?i)</?\s*(?:address|article|aside|blockquote|div|footer|h[1-6]|"
        r"header|hr|li|main|ol|p|pre|section|table|tr|ul)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _posted_at(value):
    if not value:
        return None

    match = re.fullmatch(
        r"[A-Za-z]{3}, (\d{2}) ([A-Za-z]{3}) (\d{4}) "
        r"(\d{2}):(\d{2}):(\d{2}) ([+-])(\d{2})(\d{2})",
        value,
    )
    if not match:
        return None

    months = {
        "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
        "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
        "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
    }
    day, month_name, year, hour, minute, second, sign, off_hour, off_minute = (
        match.groups()
    )
    month = months.get(month_name)
    if not month:
        return None

    return (
        year + "-" + month + "-" + day + "T"
        + hour + ":" + minute + ":" + second
        + sign + off_hour + ":" + off_minute
    )


def crawl(ctx):
    root = ET.fromstring(ctx.fetch(SITE))

    for item in root.findall("./channel/item"):
        url = _text(item, "link")
        location_names = []

        path = "./{%s}locations/{%s}location" % (TT, TT)
        for location_node in item.findall(path):
            name = _text(location_node, "{%s}name" % TT)
            if name:
                location_names.append(name)

        remote_status = _text(item, "remoteStatus")
        workplace_types = {
            "fully": "remote",
            "hybrid": "hybrid",
            "none": "on-site",
        }

        fields = {
            "url": url,
            "title": _text(item, "title"),
            "company": "Intruder",
            "location": location_names[0] if location_names else "Remote",
            "description": _html_to_text(_text(item, "description")),
            "source": SITE,
            "external_id": _text(item, "guid"),
            "department": _text(item, "{%s}department" % TT),
            "posted_at": _posted_at(_text(item, "pubDate")),
            "workplace_type": workplace_types.get(remote_status),
            "apply_url": url,
        }

        if location_names:
            fields["locations"] = location_names

        yield {
            key: value
            for key, value in fields.items()
            if value is not None
        }
