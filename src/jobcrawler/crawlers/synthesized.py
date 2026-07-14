
from html import unescape
import re
import xml.etree.ElementTree as ET

SITE = "https://synthesized.teamtailor.com/jobs.rss"
EXTRA_HOSTS = []

_TT = "https://teamtailor.com/locations"
_BLOCK_TAGS = re.compile(
    r"</?(?:address|article|aside|blockquote|br|div|footer|h[1-6]|header|hr|li|main|"
    r"ol|p|pre|section|table|tr|ul)(?:\s[^>]*)?/?>",
    re.IGNORECASE,
)
_ALL_TAGS = re.compile(r"<[^>]+>")
_MONTHS = {
    "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
    "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
    "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
}


def _description_text(value):
    text = unescape(value or "")
    text = _BLOCK_TAGS.sub("\n", text)
    text = _ALL_TAGS.sub("", text)
    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _rfc822_iso(value):
    match = re.match(
        r"^[A-Za-z]{3},\s+(\d{2})\s+([A-Za-z]{3})\s+(\d{4})\s+"
        r"(\d{2}:\d{2}:\d{2})\s+([+-])(\d{2})(\d{2})$",
        value,
    )
    if not match:
        return None
    day, month_name, year, clock, sign, zone_hour, zone_minute = match.groups()
    month = _MONTHS.get(month_name)
    if not month:
        return None
    return (
        year + "-" + month + "-" + day + "T" + clock
        + sign + zone_hour + ":" + zone_minute
    )


def _fetch_text(ctx, url):
    response = ctx.fetch(url)
    return response.text if hasattr(response, "text") else response


def crawl(ctx):
    root = ET.fromstring(_fetch_text(ctx, SITE))

    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        description = _description_text(item.findtext("description"))
        if not title or not url or not description:
            continue

        locations = []
        for node in item.findall("{%s}locations/{%s}location" % (_TT, _TT)):
            name = (node.findtext("{%s}name" % _TT) or "").strip()
            if name and name not in locations:
                locations.append(name)

        values = {
            "url": url,
            "title": title,
            "company": "Synthesized",
            "location": locations[0] if locations else None,
            "locations": locations or None,
            "description": description,
            "source": SITE,
            "apply_url": url,
        }

        guid = (item.findtext("guid") or "").strip()
        if guid:
            values["external_id"] = guid

        department = (item.findtext("{%s}department" % _TT) or "").strip()
        if department:
            values["department"] = department

        remote_status = (item.findtext("remoteStatus") or "").strip().lower()
        workplace_types = {
            "hybrid": "hybrid",
            "fully": "remote",
            "none": "on-site",
        }
        if remote_status in workplace_types:
            values["workplace_type"] = workplace_types[remote_status]

        published = _rfc822_iso((item.findtext("pubDate") or "").strip())
        if published:
            values["updated_at"] = published

        yield values
