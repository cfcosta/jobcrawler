import html
import re
import xml.etree.ElementTree as ET


SITE = "https://careers.britepayments.com/jobs.rss"
EXTRA_HOSTS = []

_TT = "https://teamtailor.com/locations"


def _plain_text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)</?(?:p|div|section|article|h[1-6]|ul|ol|li|br|hr|tr|"
        r"blockquote|pre|table|thead|tbody|tfoot|dl|dt|dd)[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line)

_MONTHS = {
    "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
    "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
    "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
}


def _iso_date(value):
    if not value:
        return None
    match = re.match(
        r"^[A-Za-z]{3}, (\d{1,2}) ([A-Za-z]{3}) (\d{4}) "
        r"(\d{2}:\d{2}:\d{2}) ([+-])(\d{2})(\d{2})$",
        value,
    )
    if not match:
        return None
    day, month, year, clock, sign, zone_hour, zone_minute = match.groups()
    month_number = _MONTHS.get(month)
    if not month_number:
        return None
    return (
        year + "-" + month_number + "-" + day.zfill(2) + "T" + clock
        + sign + zone_hour + ":" + zone_minute
    )


def _value(item, name):
    value = item.findtext(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def crawl(ctx):
    root = ET.fromstring(ctx.fetch(SITE))
    ns = {"tt": _TT}
    workplace_types = {
        "none": "onsite",
        "hybrid": "hybrid",
        "fully": "remote",
    }

    for item in root.findall("./channel/item"):
        location_names = []
        for node in item.findall("./tt:locations/tt:location", ns):
            name = node.findtext("tt:name", default="", namespaces=ns).strip()
            if name:
                location_names.append(name)

        kwargs = {
            "source": SITE,
            "url": _value(item, "link"),
            "title": _value(item, "title"),
            "company": "Brite Payments",
            "description": _plain_text(item.findtext("description") or ""),
        }

        if location_names:
            kwargs["location"] = location_names[0]
            kwargs["locations"] = location_names
        else:
            kwargs["location"] = "Not specified"

        department = _value(item, "{%s}department" % _TT)
        if department:
            kwargs["department"] = department

        external_id = _value(item, "guid")
        if external_id:
            kwargs["external_id"] = external_id

        posted_at = _iso_date(_value(item, "pubDate"))
        if posted_at:
            kwargs["posted_at"] = posted_at

        remote_status = _value(item, "remoteStatus")
        workplace_type = workplace_types.get(
            remote_status.lower() if remote_status else None
        )
        if workplace_type:
            kwargs["workplace_type"] = workplace_type

        yield kwargs
