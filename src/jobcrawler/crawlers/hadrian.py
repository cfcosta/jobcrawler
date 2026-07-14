
import html
import re
import xml.etree.ElementTree as ET

SITE = "https://careers.hadrian.io/jobs.rss"
EXTRA_HOSTS = []

TT = "https://teamtailor.com/locations"


def _plain_text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)<\s*(?:br|hr)\s*/?\s*>|</?\s*(?:address|article|aside|"
        r"blockquote|div|footer|h[1-6]|header|li|main|ol|p|pre|section|"
        r"table|td|th|tr|ul)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _location(node):
    name = (node.findtext("{" + TT + "}name") or "").strip()
    city = (node.findtext("{" + TT + "}city") or "").strip()
    country = (node.findtext("{" + TT + "}country") or "").strip()
    if name:
        return name
    return ", ".join(part for part in (city, country) if part)


def crawl(ctx):
    root = ET.fromstring(ctx.fetch(SITE))
    channel = root.find("channel")
    company = (channel.findtext("title") or "").strip() or "Hadrian"

    for item in channel.findall("item"):
        url = (item.findtext("link") or "").strip()
        locations = [
            value
            for value in (
                _location(node)
                for node in item.findall(
                    "./{" + TT + "}locations/{" + TT + "}location"
                )
            )
            if value
        ]

        remote_status = (item.findtext("remoteStatus") or "").strip().lower()
        workplace_type = {
            "fully": "remote",
            "hybrid": "hybrid",
            "none": "on-site",
        }.get(remote_status)

        yield {
            "url": url,
            "source": SITE,
            "title": (item.findtext("title") or "").strip(),
            "company": company,
            "location": locations[0] if locations else "Remote",
            "locations": locations or None,
            "description": _plain_text(item.findtext("description")),
            "workplace_type": workplace_type,
            "department": (
                item.findtext("{" + TT + "}department") or ""
            ).strip() or None,
            "external_id": (item.findtext("guid") or "").strip() or None,
            "apply_url": url,
        }
