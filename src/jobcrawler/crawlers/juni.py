
import html
import json
import re
from urllib.parse import urlparse
from xml.etree import ElementTree


SITE = "https://careers.juni.co/jobs.json"
SITEMAP = "https://careers.juni.co/sitemap.xml"
EXTRA_HOSTS = []


def plain_text(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"<\s*(?:br|/p|/div|/li|/ul|/ol|/h[1-6]|/section|/article|/tr)\s*/?>",
        "\n",
        text,
        flags=re.I,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    lines = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def job_postings(value):
    found = []
    if isinstance(value, list):
        for item in value:
            found.extend(job_postings(item))
    elif isinstance(value, dict):
        kind = value.get("@type")
        kinds = kind if isinstance(kind, list) else [kind]
        if "JobPosting" in kinds:
            found.append(value)
        if "@graph" in value:
            found.extend(job_postings(value["@graph"]))
    return found


def address_text(place):
    if not isinstance(place, dict):
        return None
    address = place.get("address", place)
    if isinstance(address, str):
        return html.unescape(address).strip() or None
    if not isinstance(address, dict):
        return None

    parts = []
    for key in (
        "streetAddress",
        "addressLocality",
        "addressRegion",
        "postalCode",
        "addressCountry",
    ):
        value = address.get(key)
        if value is not None:
            value = html.unescape(str(value)).strip()
            if value and value not in parts:
                parts.append(value)
    return ", ".join(parts) if parts else None


def crawl(ctx):
    sitemap_xml = ctx.fetch(SITEMAP)
    root = ElementTree.fromstring(sitemap_xml)

    urls = []
    seen = set()
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] != "loc" or not node.text:
            continue
        url = html.unescape(node.text.strip())
        parsed = urlparse(url)
        if (
            parsed.scheme == "https"
            and parsed.netloc == "careers.juni.co"
            and re.fullmatch(r"/jobs/\d+-[^/?#]+", parsed.path)
            and url not in seen
        ):
            seen.add(url)
            urls.append(url)

    for url in urls:
        page = ctx.fetch(url)
        scripts = re.findall(
            r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            page,
            flags=re.I | re.S,
        )

        posting = None
        for script in scripts:
            try:
                candidates = job_postings(json.loads(script))
            except (TypeError, ValueError):
                continue
            if candidates:
                posting = candidates[0]
                break
        if not posting:
            continue

        title = html.unescape(str(posting.get("title") or "")).strip()
        description = plain_text(posting.get("description"))
        if not title or not description:
            continue

        raw_locations = posting.get("jobLocation")
        if not isinstance(raw_locations, list):
            raw_locations = [raw_locations] if raw_locations else []

        locations = []
        for raw_location in raw_locations:
            location_value = address_text(raw_location)
            if location_value and location_value not in locations:
                locations.append(location_value)

        data = {
            "title": title,
            "company": "Juni Technology",
            "description": description,
            "location": locations[0] if locations else None,
            "locations": locations or None,
            "employment_type": posting.get("employmentType"),
            "posted_at": posting.get("datePosted"),
        }

        identifier = posting.get("identifier")
        if isinstance(identifier, dict):
            data["external_id"] = identifier.get("value")
        elif identifier is not None:
            data["external_id"] = identifier

        data = {
            key: value
            for key, value in data.items()
            if value is not None and value != ""
        }
        data["url"] = url
        data["source"] = SITE
        yield data
