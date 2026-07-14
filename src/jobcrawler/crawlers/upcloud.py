import html
import re
import xml.etree.ElementTree as ET


SITE = "https://upcloud.teamtailor.com/jobs.rss"
EXTRA_HOSTS = []

TT = "https://teamtailor.com/locations"

MONTHS = {
    "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
    "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
    "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
}


def rss_date_to_iso(value):
    match = re.match(
        r"^[A-Za-z]{3}, (\d{2}) ([A-Za-z]{3}) (\d{4}) "
        r"(\d{2}:\d{2}:\d{2}) ([+-])(\d{2})(\d{2})$",
        value,
    )
    if not match or match.group(2) not in MONTHS:
        return None
    return "%s-%s-%sT%s%s%s:%s" % (
        match.group(3),
        MONTHS[match.group(2)],
        match.group(1),
        match.group(4),
        match.group(5),
        match.group(6),
        match.group(7),
    )


def html_to_text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/ul|/ol|/h[1-6]|/tr|/table)\s*/?\s*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def crawl(ctx):
    response = ctx.fetch(SITE)
    root = ET.fromstring(response)

    workplace_types = {
        "fully": "remote",
        "remote": "remote",
        "hybrid": "hybrid",
        "none": "on-site",
    }

    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        description = html_to_text(item.findtext("description"))

        locations = []
        path = "./{%s}locations/{%s}location" % (TT, TT)
        for node in item.findall(path):
            name = (node.findtext("{%s}name" % TT) or "").strip()
            if name:
                locations.append(name)

        if not title or not url or not description or not locations:
            continue

        data = {
            "url": url,
            "title": title,
            "company": "UpCloud",
            "location": locations[0],
            "locations": locations,
            "description": description,
            "source": SITE,
        }

        department = (item.findtext("{%s}department" % TT) or "").strip()
        if department:
            data["department"] = department

        external_id = (item.findtext("guid") or "").strip()
        if external_id:
            data["external_id"] = external_id

        posted_at = (item.findtext("pubDate") or "").strip()
        if posted_at:
            posted_at_iso = rss_date_to_iso(posted_at)
            if posted_at_iso:
                data["posted_at"] = posted_at_iso

        remote_status = (item.findtext("remoteStatus") or "").strip().lower()
        if remote_status in workplace_types:
            data["workplace_type"] = workplace_types[remote_status]

        yield data
