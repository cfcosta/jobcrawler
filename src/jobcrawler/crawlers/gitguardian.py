import re
import html
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

SITE = "https://careers.gitguardian.com/jobs.rss"
EXTRA_HOSTS = []

TT_NS = {"tt": "https://teamtailor.com/locations"}

# Teamtailor `remoteStatus` -> workplace_type
_WORKPLACE = {
    "hybrid": "Hybrid",
    "fully": "Remote",
    "remote": "Remote",
    "temporary": "Remote",
    "temporarily": "Remote",
    "none": "On-site",
}


def _html_to_text(raw_html):
    text = raw_html or ""
    text = re.sub(r"(?is)<(script|style|svg)\b.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", " ", text)
    text = re.sub(
        r"(?i)</?(p|div|li|ul|ol|h1|h2|h3|h4|section|article|blockquote)\b[^>]*>",
        " ",
        text,
    )
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _location_string(loc):
    def t(tag):
        return (loc.findtext("tt:" + tag, default="", namespaces=TT_NS) or "").strip()

    city, country, name = t("city"), t("country"), t("name")
    if city and country and city.lower() != country.lower():
        return f"{city}, {country}"
    return city or name or country or ""


def _external_id(url):
    path = urlparse(url).path.rstrip("/")
    last = path.rsplit("/", 1)[-1] if path else ""
    m = re.match(r"(\d+)", last)
    return m.group(1) if m else None


def crawl(ctx):
    xml_text = ctx.fetch(SITE)
    root = ET.fromstring(xml_text)
    channel = root.find("channel")
    if channel is None:
        return

    for item in channel.findall("item"):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        description = _html_to_text(item.findtext("description") or "")

        if not title or not url or not description:
            continue

        # Structured locations from the tt: namespace.
        locations = []
        for loc in item.findall("tt:locations/tt:location", TT_NS):
            loc_str = _location_string(loc)
            if loc_str and loc_str not in locations:
                locations.append(loc_str)

        remote_status = (item.findtext("remoteStatus") or "").strip().lower()
        workplace_type = _WORKPLACE.get(remote_status)

        location = (
            locations[0]
            if locations
            else ("Remote" if workplace_type == "Remote" else "")
        )

        job = {
            "url": url,
            "title": title,
            "company": "GitGuardian",
            "location": location,
            "description": description,
            "source": SITE,
        }

        external_id = _external_id(url)
        if external_id:
            job["external_id"] = external_id
        if len(locations) > 1:
            job["locations"] = locations
        if workplace_type:
            job["workplace_type"] = workplace_type

        department = (
            item.findtext("tt:department", default="", namespaces=TT_NS) or ""
        ).strip()
        if department:
            job["department"] = department

        posted_at = (item.findtext("pubDate") or "").strip()
        if posted_at:
            job["posted_at"] = posted_at

        yield job
