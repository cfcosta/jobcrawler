
import html
import json
import re
from urllib.parse import urlparse


SITE = "https://careers.juni.co/jobs.json"
EXTRA_HOSTS = []
SITEMAP = "https://careers.juni.co/sitemap.xml"


def _plain_text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"<(?:br|/p|/div|/li|/ul|/ol|/h[1-6]|/section|/article)\b[^>]*>",
        "\n",
        value,
        flags=re.I,
    )
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{2,}", "\n\n", value)
    return value.strip()


def _objects(value):
    if isinstance(value, dict):
        yield value
        graph = value.get("@graph")
        if graph is not None:
            for item in _objects(graph):
                yield item
    elif isinstance(value, list):
        for value_item in value:
            for item in _objects(value_item):
                yield item


def _jobposting(page):
    blocks = re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        page,
        flags=re.I | re.S,
    )
    for block in blocks:
        try:
            data = json.loads(block)
        except (TypeError, ValueError):
            continue
        for obj in _objects(data):
            kinds = obj.get("@type")
            if kinds == "JobPosting" or (
                isinstance(kinds, list) and "JobPosting" in kinds
            ):
                return obj
    return None


def _location(place):
    if not isinstance(place, dict):
        return None

    address = place.get("address", place)
    if isinstance(address, str):
        return _plain_text(address) or None
    if not isinstance(address, dict):
        return None

    parts = []
    for key in (
        "streetAddress",
        "postalCode",
        "addressLocality",
        "addressRegion",
        "addressCountry",
    ):
        value = address.get(key)
        if value is not None:
            text = _plain_text(str(value))
            if text and text not in parts:
                parts.append(text)
    return ", ".join(parts) or None


def _identifier(value):
    if isinstance(value, dict):
        value = value.get("value")
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def crawl(ctx):
    sitemap_text = ctx.fetch(SITEMAP)
    sitemap_urls = re.findall(
        r"<loc>\s*(.*?)\s*</loc>",
        sitemap_text,
        flags=re.I | re.S,
    )

    seen = set()
    for raw_url in sitemap_urls:
        url = html.unescape(raw_url.strip())
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "careers.juni.co"
            or not re.fullmatch(r"/jobs/\d+-[^/]+", parsed.path)
            or url in seen
        ):
            continue
        seen.add(url)

        posting = _jobposting(ctx.fetch(url))
        if not posting:
            continue

        title = _plain_text(str(posting.get("title") or ""))
        description = _plain_text(str(posting.get("description") or ""))

        organization = posting.get("hiringOrganization")
        if isinstance(organization, dict):
            company = _plain_text(str(organization.get("name") or ""))
        else:
            company = ""
        company = company or "Juni Technology"

        raw_places = posting.get("jobLocation") or []
        if isinstance(raw_places, dict):
            raw_places = [raw_places]

        locations = []
        for place in raw_places:
            location = _location(place)
            if location and location not in locations:
                locations.append(location)

        if not title or not description or not locations:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": company,
            "location": locations[0],
            "locations": locations,
            "description": description,
            "source": SITE,
        }

        optional = {
            "employment_type": posting.get("employmentType"),
            "posted_at": posting.get("datePosted"),
            "expires_at": posting.get("validThrough"),
            "external_id": _identifier(posting.get("identifier")),
        }
        for key, value in optional.items():
            if value is not None and str(value).strip():
                fields[key] = value

        yield fields
