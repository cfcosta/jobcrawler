
import html
import re
from xml.etree import ElementTree as ET

SITE = "https://careers.spectrocloud.com/jobs.rss"
EXTRA_HOSTS = []


def _description(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)\s*>",
        " ",
        value,
    )
    value = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|dd|div|dl|dt|"
        r"figcaption|figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|"
        r"section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()

def _local_name(tag):
    return tag.rsplit("}", 1)[-1]


def _child_text(element, name):
    for child in element:
        if _local_name(child.tag) == name:
            value = (child.text or "").strip()
            return value or None
    return None


def _location_value(location):
    parts = {
        _local_name(child.tag): (child.text or "").strip()
        for child in location
    }
    # Teamtailor's name is the source's primary display value. Some records
    # leave it blank, so retain their structured city/country information.
    if parts.get("name"):
        return parts["name"]
    city = parts.get("city")
    country = parts.get("country")
    if city and city != "-" and country:
        return city if city.casefold() == country.casefold() else city + ", " + country
    if city and city != "-":
        return city
    return country or parts.get("address") or None


def _locations(item):
    values = []
    for child in item:
        if _local_name(child.tag) != "locations":
            continue
        for location in child:
            value = _location_value(location)
            if value and value not in values:
                values.append(value)
    return values


def _posted_at(value):
    if not value:
        return None
    match = re.match(
        r"^[A-Za-z]{3}, (\d{2}) ([A-Za-z]{3}) (\d{4}) "
        r"(\d{2}):(\d{2}):(\d{2}) ([+-])(\d{2})(\d{2})$",
        value,
    )
    if not match:
        return None
    months = {
        "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
        "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
        "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
    }
    day, month_name, year, hour, minute, second, sign, tz_hour, tz_minute = (
        match.groups()
    )
    month = months.get(month_name)
    if not month:
        return None
    return (
        year + "-" + month + "-" + day + "T" + hour + ":" + minute + ":"
        + second + sign + tz_hour + ":" + tz_minute
    )

def crawl(ctx):
    root = ET.fromstring(ctx.fetch(SITE))
    workplace_types = {
        "fully": "remote",
        "remote": "remote",
        "hybrid": "hybrid",
        "none": "on-site",
    }

    for item in root.findall("./channel/item"):
        title = _child_text(item, "title")
        url = _child_text(item, "link")
        description = _description(_child_text(item, "description"))
        locations = _locations(item)
        remote_status = _child_text(item, "remoteStatus")
        role = _child_text(item, "role")

        data = {
            "url": url,
            "title": title,
            "company": "Spectro Cloud",
            "source": SITE,
            "location": locations[0] if locations else "Unspecified",
            "description": description,
            "external_id": _child_text(item, "guid"),
            "posted_at": _posted_at(_child_text(item, "pubDate")),
            "department": _child_text(item, "department"),
            "employment_type": role,
            "workplace_type": workplace_types.get(
                remote_status.lower() if remote_status else None
            ),
            "apply_url": url,
        }
        if locations:
            data["locations"] = locations

        yield {key: value for key, value in data.items() if value is not None}
