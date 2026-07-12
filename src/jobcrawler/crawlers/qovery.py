
import html
import re
import xml.etree.ElementTree as ET


SITE = "https://jobs.qovery.com/jobs.rss"
EXTRA_HOSTS = []

TT = "https://teamtailor.com/locations"

MONTHS = {
    "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
    "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
    "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
}


def _posted_at(value):
    match = re.fullmatch(
        r"[A-Za-z]{3}, (\d{2}) ([A-Za-z]{3}) (\d{4}) "
        r"(\d{2}:\d{2}:\d{2}) ([+-])(\d{2})(\d{2})",
        value or "",
    )
    if not match:
        return None
    day, month, year, clock, sign, offset_hour, offset_minute = match.groups()
    month_number = MONTHS.get(month)
    if not month_number:
        return None
    return (
        year + "-" + month_number + "-" + day + "T" + clock
        + sign + offset_hour + ":" + offset_minute
    )



def _plain_text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)<\s*(?:br|hr)\s*/?\s*>|<\s*/?\s*(?:address|article|aside|"
        r"blockquote|div|dl|dt|dd|figcaption|figure|footer|h[1-6]|header|"
        r"li|main|ol|p|pre|section|table|tr|ul)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def crawl(ctx):
    root = ET.fromstring(ctx.fetch(SITE))

    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        description = _plain_text(item.findtext("description"))

        locations = []
        path = f"{{{TT}}}locations/{{{TT}}}location/{{{TT}}}name"
        for node in item.findall(path):
            name = (node.text or "").strip()
            if name and name not in locations:
                locations.append(name)

        kwargs = {
            "url": url,
            "title": title,
            "company": "Qovery",
            "location": locations[0] if locations else "Remote",
            "description": description,
            "source": SITE,
            "apply_url": url,
        }

        if locations:
            kwargs["locations"] = locations

        guid = (item.findtext("guid") or "").strip()
        if guid:
            kwargs["external_id"] = guid

        department = (item.findtext(f"{{{TT}}}department") or "").strip()
        if department:
            kwargs["department"] = department

        remote_status = (item.findtext("remoteStatus") or "").strip().lower()
        if remote_status == "fully":
            kwargs["workplace_type"] = "Remote"

        posted_at = _posted_at((item.findtext("pubDate") or "").strip())
        if posted_at:
            kwargs["posted_at"] = posted_at

        if title and url and description:
            yield kwargs
