import html
import re
import xml.etree.ElementTree as ET


SITE = "https://careers.victoriametrics.com/jobs.rss"
EXTRA_HOSTS = []

TT = "https://teamtailor.com/locations"



MONTHS = {
    "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
    "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
    "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
}


def _rfc822_to_iso(value):
    parts = value.replace(",", "").split()
    if len(parts) != 6 or parts[2] not in MONTHS:
        return None

    day = parts[1].zfill(2)
    month = MONTHS[parts[2]]
    year = parts[3]
    clock = parts[4]
    offset = parts[5]
    if len(offset) == 5 and offset[0] in "+-":
        offset = offset[:3] + ":" + offset[3:]
    else:
        return None

    return "%s-%s-%sT%s%s" % (year, month, day, clock, offset)


def _text(element, path):
    child = element.find(path)
    if child is None:
        return None
    value = (child.text or "").strip()
    return value or None


def _description(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)<\s*(?:br|hr)\s*/?\s*>|</?\s*(?:p|div|li|h[1-6]|section|"
        r"article|blockquote|tr|table|ul|ol|pre)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def crawl(ctx):
    response = ctx.fetch(SITE)
    root = ET.fromstring(response)

    seen = set()
    for item in root.findall("./channel/item"):
        title = _text(item, "title")
        url = _text(item, "link")
        description_node = item.find("description")
        raw_description = (
            description_node.text if description_node is not None else None
        )
        description = _description(raw_description)
        external_id = _text(item, "guid")

        dedupe_key = external_id or url
        if not dedupe_key or dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        location_names = []
        location_path = "./{%s}locations/{%s}location" % (TT, TT)
        for location_node in item.findall(location_path):
            name = _text(location_node, "{%s}name" % TT)
            if name and name not in location_names:
                location_names.append(name)

        if not location_names:
            continue

        data = {
            "url": url,
            "title": title,
            "company": "VictoriaMetrics",
            "location": location_names[0],
            "description": description,
            "source": SITE,
            "apply_url": url,
        }

        if location_names:
            data["locations"] = location_names

        department = _text(item, "{%s}department" % TT)
        if department:
            data["department"] = department

        posted_at = _text(item, "pubDate")
        if posted_at:
            posted_at_iso = _rfc822_to_iso(posted_at)
            if posted_at_iso:
                data["posted_at"] = posted_at_iso

        if external_id:
            data["external_id"] = external_id

        remote_status = _text(item, "remoteStatus")
        if remote_status:
            data["workplace_type"] = (
                "remote" if remote_status.lower() == "fully" else remote_status
            )

        if title and url and description:
            yield data
