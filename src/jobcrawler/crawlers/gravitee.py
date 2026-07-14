
import datetime
import re
import xml.etree.ElementTree as ET


SITE = "https://graviteetopcoltd.teamtailor.com/jobs.rss"
EXTRA_HOSTS = []
TT_NS = "https://teamtailor.com/locations"
NS = {"tt": TT_NS}
MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}
NAMED_ENTITIES = {
    "amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'",
    "nbsp": " ", "ndash": "-", "mdash": "-", "hellip": "...",
}


def _entity(match):
    value = match.group(1)
    if value.startswith("#x") or value.startswith("#X"):
        try:
            return chr(int(value[2:], 16))
        except ValueError:
            return match.group(0)
    if value.startswith("#"):
        try:
            return chr(int(value[1:]))
        except ValueError:
            return match.group(0)
    return NAMED_ENTITIES.get(value, match.group(0))


def _html_to_text(value):
    value = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|dd|div|dl|dt|figcaption|figure|footer|h[1-6]|header|hr|li|main|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)[^>]*>",
        "\n",
        value or "",
    )
    value = re.sub(
        r"(?is)<(?:script|style|noscript)\b[^>]*>.*?</(?:script|style|noscript)>",
        "",
        value,
    )
    value = re.sub(r"(?s)<[^>]+>", "", value)
    value = re.sub(r"&([A-Za-z]+|#[0-9]+|#[xX][0-9A-Fa-f]+);", _entity, value)

    lines = []
    for line in value.splitlines():
        clean = " ".join(line.split())
        if clean:
            lines.append(clean)
    return "\n".join(lines)


def _posted_at(value):
    match = re.match(
        r"^[A-Za-z]{3}, (\d{1,2}) ([A-Za-z]{3}) (\d{4}) "
        r"(\d{2}):(\d{2}):(\d{2}) ([+-])(\d{2})(\d{2})$",
        value or "",
    )
    if not match:
        return None

    day, month_name, year, hour, minute, second, sign, zone_hour, zone_minute = match.groups()
    month = MONTHS.get(month_name)
    if month is None:
        return None

    offset_minutes = int(zone_hour) * 60 + int(zone_minute)
    if sign == "-":
        offset_minutes = -offset_minutes

    try:
        zone = datetime.timezone(datetime.timedelta(minutes=offset_minutes))
        parsed = datetime.datetime(
            int(year), month, int(day), int(hour), int(minute), int(second),
            tzinfo=zone,
        )
        return parsed.isoformat()
    except ValueError:
        return None


def _locations(item):
    parent = item.find("tt:locations", NS)
    if parent is None:
        return []

    values = []
    for node in parent.findall("tt:location", NS):
        name = (node.findtext("tt:name", default="", namespaces=NS) or "").strip()
        city = (node.findtext("tt:city", default="", namespaces=NS) or "").strip()
        country = (node.findtext("tt:country", default="", namespaces=NS) or "").strip()
        display = name or city or country
        if display and display not in values:
            values.append(display)
    return values


def crawl(ctx):
    root = ET.fromstring(ctx.fetch(SITE))
    channel = root.find("channel")
    if channel is None:
        return

    for item in channel.findall("item"):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        description = _html_to_text(item.findtext("description") or "")
        locations = _locations(item)
        remote_status = (item.findtext("remoteStatus") or "").strip().lower()

        if not title or not url or not description:
            continue

        if locations:
            location = locations[0]
        elif remote_status == "fully":
            location = "Remote"
        elif remote_status:
            location = remote_status.capitalize()
        else:
            continue

        fields = {
            "source": SITE,
            "url": url,
            "title": title,
            "company": "Gravitee",
            "location": location,
            "description": description,
        }

        if locations:
            fields["locations"] = locations

        if remote_status == "fully":
            fields["workplace_type"] = "Remote"
        elif remote_status == "hybrid":
            fields["workplace_type"] = "Hybrid"

        department = (
            item.findtext("tt:department", default="", namespaces=NS) or ""
        ).strip()
        if department:
            fields["department"] = department

        guid = (item.findtext("guid") or "").strip()
        if guid:
            fields["external_id"] = guid

        posted_at = _posted_at((item.findtext("pubDate") or "").strip())
        if posted_at:
            fields["posted_at"] = posted_at

        yield fields
