
import html
import re
import xml.etree.ElementTree as ET

SITE = "https://career.mangopay.com/jobs.rss"
EXTRA_HOSTS = []

TT = "https://teamtailor.com/locations"

MONTHS = {
    "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
    "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
    "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
}


def _posted_at(value):
    match = re.match(
        r"^[A-Za-z]{3}, (\d{2}) ([A-Za-z]{3}) (\d{4}) "
        r"(\d{2}:\d{2}:\d{2}) ([+-]\d{2})(\d{2})$",
        value,
    )
    if not match:
        return None
    day, month, year, clock, offset_hour, offset_minute = match.groups()
    month_number = MONTHS.get(month)
    if not month_number:
        return None
    return (
        year + "-" + month_number + "-" + day + "T" + clock
        + offset_hour + ":" + offset_minute
    )


def _plain_text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|"
        r"figcaption|figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|"
        r"section|table|tr|ul)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?s)<script\b[^>]*>.*?</script>", "", value)
    value = re.sub(r"(?s)<style\b[^>]*>.*?</style>", "", value)
    value = re.sub(r"(?s)<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def crawl(ctx):
    root = ET.fromstring(ctx.fetch(SITE))
    channel = root.find("channel")
    if channel is None:
        return

    for item in channel.findall("item"):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        description = _plain_text(item.findtext("description"))
        if not title or not url or not description:
            continue

        location_names = []
        locations_node = item.find("{%s}locations" % TT)
        if locations_node is not None:
            for node in locations_node.findall("{%s}location" % TT):
                name = (node.findtext("{%s}name" % TT) or "").strip()
                if name:
                    location_names.append(name)

        id_match = re.search(r"/jobs/(\d+)(?:-|$)", url)
        department = (item.findtext("{%s}department" % TT) or "").strip()
        remote_status = (item.findtext("remoteStatus") or "").strip()
        if not location_names:
            continue

        kwargs = {
            "url": url,
            "title": title,
            "company": "Mangopay",
            "location": location_names[0],
            "description": description,
            "source": SITE,
        }
        if location_names:
            kwargs["locations"] = location_names
        if department:
            kwargs["department"] = department
        if remote_status:
            kwargs["workplace_type"] = remote_status
        if id_match:
            kwargs["external_id"] = id_match.group(1)

        pub_date = (item.findtext("pubDate") or "").strip()
        if pub_date:
            normalized_date = _posted_at(pub_date)
            if normalized_date:
                kwargs["posted_at"] = normalized_date

        yield kwargs
