import html
import re
import xml.etree.ElementTree as ET

SITE = "https://careers.civo.com/jobs.rss"
EXTRA_HOSTS = []

_TT = "{https://teamtailor.com/locations}"

_BR_RE = re.compile(r"(?i)<\s*br\s*/?\s*>")
_BLOCK_RE = re.compile(r"(?i)</\s*(?:p|div|li|h[1-6]|ul|ol|tr|table|section)\s*>")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v ]+")


def _clean(raw):
    if not raw:
        return ""
    text = _BR_RE.sub("\n", raw)
    text = _BLOCK_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_WORKPLACE = {
    "fully": "remote",
    "temporary": "remote",
    "hybrid": "hybrid",
    "none": "onsite",
}


def _location(item):
    remote = (item.findtext("remoteStatus") or "").strip().lower()
    locs = []
    container = item.find(f"{_TT}locations")
    if container is not None:
        for child in container:
            value = (child.text or "").strip()
            if value and value not in locs:
                locs.append(value)
        direct = (container.text or "").strip()
        if direct and direct not in locs:
            locs.append(direct)
    if locs:
        location = ", ".join(locs)
    elif remote in ("fully", "temporary"):
        location = "Remote"
    elif remote == "hybrid":
        location = "Hybrid"
    else:
        location = ""
    return location, locs, _WORKPLACE.get(remote, "")


def crawl(ctx):
    root = ET.fromstring(ctx.fetch(SITE))
    channel = root.find("channel")
    if channel is None:
        return
    for item in channel.findall("item"):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        description = _clean(item.findtext("description"))
        if not (title and url and description):
            continue

        location, locations, workplace_type = _location(item)
        if not location:
            continue

        out = {
            "url": url,
            "title": title,
            "company": "Civo",
            "location": location,
            "description": description,
            "source": SITE,
        }

        match = re.search(r"/jobs/(\d+)", url)
        external_id = match.group(1) if match else (item.findtext("guid") or "").strip()
        if external_id:
            out["external_id"] = external_id

        if len(locations) > 1:
            out["locations"] = locations

        if workplace_type:
            out["workplace_type"] = workplace_type

        department = (item.findtext(f"{_TT}department") or "").strip()
        if department:
            out["department"] = department

        posted_at = (item.findtext("pubDate") or "").strip()
        if posted_at:
            out["posted_at"] = posted_at

        yield out
