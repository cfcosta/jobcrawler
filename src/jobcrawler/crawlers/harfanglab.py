
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET

SITE = "https://harfanglab-1666711819.teamtailor.com/jobs.rss"
EXTRA_HOSTS = []

_TT = "https://teamtailor.com/locations"


def _description(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)<\s*(?:br|hr)\s*/?\s*>|</?\s*(?:address|article|aside|blockquote|dd|div|dl|dt|figcaption|footer|h[1-6]|header|li|main|ol|p|pre|section|table|tbody|td|th|thead|tr|ul)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?s)<script\b[^>]*>.*?</script>", " ", value)
    value = re.sub(r"(?s)<style\b[^>]*>.*?</style>", " ", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _text(parent, tag):
    node = parent.find(tag)
    if node is None or node.text is None:
        return None
    value = node.text.strip()
    return value or None



def _rss_date(value):
    if not value:
        return None
    months = {
        "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
        "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
        "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
    }
    parts = value.split()
    if len(parts) != 6:
        return None
    day = parts[1].zfill(2)
    month = months.get(parts[2])
    year = parts[3]
    clock = parts[4]
    offset = parts[5]
    if not month or len(offset) != 5 or offset[0] not in "+-":
        return None
    return (
        year + "-" + month + "-" + day + "T" + clock
        + offset[:3] + ":" + offset[3:]
    )

def crawl(ctx):
    root = ET.fromstring(ctx.fetch(SITE))

    workplace_types = {
        "fully": "remote",
        "hybrid": "hybrid",
        "none": "on-site",
    }

    for item in root.findall("./channel/item"):
        title = _text(item, "title")
        url = _text(item, "link")
        description = _description(_text(item, "description"))

        locations = []
        for location_node in item.findall(
            f"{{{_TT}}}locations/{{{_TT}}}location"
        ):
            name = _text(location_node, f"{{{_TT}}}name")
            if name:
                locations.append(name)

        remote_status = _text(item, "remoteStatus")
        location = locations[0] if locations else None
        if location is None and remote_status == "fully":
            location = "Remote"

        job = {
            "url": url,
            "title": title,
            "company": "HarfangLab",
            "location": location,
            "description": description,
            "source": SITE,
            "apply_url": url,
            "external_id": _text(item, "guid"),
            "posted_at": _rss_date(_text(item, "pubDate")),
            "department": _text(item, f"{{{_TT}}}department"),
            "workplace_type": workplace_types.get(remote_status),
        }
        if locations:
            job["locations"] = locations

        yield {key: value for key, value in job.items() if value is not None}
