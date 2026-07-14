import html
import re
import xml.etree.ElementTree as ET

SITE = "https://careers.fyul.com/jobs.rss"
EXTRA_HOSTS = []

_TT = "https://teamtailor.com/locations"
_BLOCK_RE = re.compile(
    r"</?(?:address|article|aside|blockquote|br|div|footer|h[1-6]|header|hr|li|"
    r"main|nav|ol|p|pre|section|table|tr|ul)\b[^>]*>",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"[^\S\n]+")
_NEWLINE_RE = re.compile(r"\n{2,}")



_MONTHS = {
    "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
    "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
    "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
}


def _rfc822_to_iso(value):
    if not value:
        return None
    parts = value.replace(",", "").split()
    if len(parts) != 6 or parts[2] not in _MONTHS:
        return None
    day = parts[1].zfill(2)
    month = _MONTHS[parts[2]]
    year = parts[3]
    clock = parts[4]
    offset = parts[5]
    if len(offset) == 5 and offset[0] in "+-":
        offset = offset[:3] + ":" + offset[3:]
    return f"{year}-{month}-{day}T{clock}{offset}"


def _html_to_text(value):
    value = html.unescape(value)
    value = _BLOCK_RE.sub("\n", value)
    value = _TAG_RE.sub("", value)
    value = html.unescape(value).replace("\r", "\n")
    value = _SPACE_RE.sub(" ", value)
    value = re.sub(r" *\n *", "\n", value)
    return _NEWLINE_RE.sub("\n", value).strip()


def _text(element, path):
    value = element.findtext(path)
    if value is None:
        return None
    value = value.strip()
    return value or None


def crawl(ctx):
    response = ctx.fetch(SITE)
    body_text = response if isinstance(response, str) else response.text
    root = ET.fromstring(body_text)

    for item in root.findall("./channel/item"):
        title = _text(item, "title")
        url = _text(item, "link")
        body = _text(item, "description")
        if not title or not url or not body:
            continue

        locations = []
        for node in item.findall(
            f"{{{_TT}}}locations/{{{_TT}}}location"
        ):
            name = _text(node, f"{{{_TT}}}name")
            if name:
                locations.append(name)

        if not locations:
            continue

        kwargs = {
            "url": url,
            "title": title,
            "company": "FYUL",
            "location": locations[0],
            "locations": locations,
            "description": _html_to_text(body),
            "source": SITE,
            "apply_url": url,
        }

        optional = {
            "department": _text(item, f"{{{_TT}}}department"),
            "workplace_type": _text(item, "remoteStatus"),
            "updated_at": _rfc822_to_iso(_text(item, "pubDate")),
            "external_id": _text(item, "guid"),
        }
        kwargs.update({key: value for key, value in optional.items() if value})
        yield kwargs
