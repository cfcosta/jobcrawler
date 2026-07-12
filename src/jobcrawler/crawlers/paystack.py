
import html
import re
import xml.etree.ElementTree as ET


SITE = "https://careers.paystack.com/jobs"
EXTRA_HOSTS = []
FEED_URL = "https://careers.paystack.com/jobs.rss"
TT_NS = "https://teamtailor.com/locations"


def _html_to_text(value):
    value = re.sub(r"(?i)<br\s*/?>", "\n", value or "")
    value = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|div|dl|dt|dd|figcaption|"
        r"figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|"
        r"td|th|tr|ul)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        clean = re.sub(r"\s+", " ", line).strip()
        if clean:
            lines.append(clean)
    return "\n".join(lines)


_MONTHS = {
    "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
    "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
    "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
}


def _rss_date_to_iso(value):
    match = re.fullmatch(
        r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun), "
        r"(\d{2}) ([A-Z][a-z]{2}) (\d{4}) "
        r"(\d{2}):(\d{2}):(\d{2}) ([+-]\d{2})(\d{2})",
        (value or "").strip(),
    )
    if not match:
        return None

    day, month_name, year, hour, minute, second, tz_hour, tz_minute = (
        match.groups()
    )
    month = _MONTHS.get(month_name)
    if not month:
        return None

    return (
        "%s-%s-%sT%s:%s:%s%s:%s"
        % (
            year, month, day, hour, minute, second, tz_hour, tz_minute
        )
    )


def crawl(ctx):
    root = ET.fromstring(ctx.fetch(FEED_URL))

    for item in root.findall("./channel/item"):
        url = (item.findtext("link") or "").strip()
        title = (item.findtext("title") or "").strip()
        description = _html_to_text(item.findtext("description"))

        if not re.fullmatch(
            r"https://careers\.paystack\.com/jobs/\d+-[^/?#]+", url
        ):
            continue
        if not title or not description:
            continue

        locations = []
        location_path = "./{%s}locations/{%s}location" % (TT_NS, TT_NS)
        name_tag = "{%s}name" % TT_NS
        for node in item.findall(location_path):
            name = (node.findtext(name_tag) or "").strip()
            if name and name not in locations:
                locations.append(name)

        department = (
            item.findtext("{%s}department" % TT_NS) or ""
        ).strip() or None
        posted_at = _rss_date_to_iso(item.findtext("pubDate"))
        id_match = re.search(r"/jobs/(\d+)-", url)

        yield {
            "url": url,
            "title": title,
            "company": "Paystack",
            "location": locations[0] if locations else None,
            "locations": locations or None,
            "description": description,
            "source": SITE,
            "department": department,
            "posted_at": posted_at,
            "external_id": id_match.group(1) if id_match else None,
            "apply_url": url,
        }
