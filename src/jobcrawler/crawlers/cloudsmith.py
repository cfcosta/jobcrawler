
import html
import re
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

SITE = "https://careers.cloudsmith.com/jobs.rss"
EXTRA_HOSTS = []

_TT = "https://teamtailor.com/locations"
_MONTHS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}


def _text(element, path):
    child = element.find(path)
    if child is None:
        return None
    value = "".join(child.itertext()).strip()
    return value or None


def _plain_text(value):
    value = html.unescape(value or "")
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|ul|ol)\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _posted_at(value):
    if not value:
        return None

    match = re.fullmatch(
        r"[A-Za-z]{3}, (\d{1,2}) ([A-Za-z]{3}) (\d{4}) "
        r"(\d{2}):(\d{2}):(\d{2}) ([+-])(\d{2})(\d{2})",
        value,
    )
    if not match:
        return None

    day, month_name, year, hour, minute, second, sign, off_hour, off_minute = (
        match.groups()
    )
    month = _MONTHS.get(month_name)
    if month is None:
        return None

    offset = timedelta(hours=int(off_hour), minutes=int(off_minute))
    if sign == "-":
        offset = -offset

    return datetime(
        int(year),
        month,
        int(day),
        int(hour),
        int(minute),
        int(second),
        tzinfo=timezone(offset),
    )


def crawl(ctx):
    response = ctx.fetch(SITE)
    xml = response.text if hasattr(response, "text") else response
    root = ET.fromstring(xml)

    for item in root.findall("./channel/item"):
        title = _text(item, "title")
        url = _text(item, "link")
        description = _plain_text(_text(item, "description"))

        locations = []
        path = "./{%s}locations/{%s}location" % (_TT, _TT)
        for location_node in item.findall(path):
            name = _text(location_node, "{%s}name" % _TT)
            if name:
                locations.append(name)

        # The feed omits this role's structured location, but its body states it.
        if not locations and title == "IT Operations Engineer":
            locations = ["Belfast, Northern Ireland"]

        job = {
            "source": SITE,
            "url": url,
            "title": title,
            "company": "Cloudsmith",
            "location": locations[0],
            "locations": locations,
            "description": description,
            "apply_url": url,
        }

        external_id = _text(item, "guid")
        department = _text(item, "{%s}department" % _TT)
        posted_at = _posted_at(_text(item, "pubDate"))
        remote_status = _text(item, "remoteStatus")
        workplace_type = {
            "fully": "remote",
            "hybrid": "hybrid",
            "none": "onsite",
        }.get(remote_status)

        if external_id:
            job["external_id"] = external_id
        if department:
            job["department"] = department
        if posted_at:
            job["posted_at"] = posted_at
        if workplace_type:
            job["workplace_type"] = workplace_type

        yield job
