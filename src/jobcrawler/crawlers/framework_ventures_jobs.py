
import json
import re
from html import unescape
from urllib.parse import urlparse


SITE = "https://jobs.framework.ventures/"
EXTRA_HOSTS = []

_ROLE_WORDS = re.compile(
    r"(?:engineer|engineering|developer|protocol|backend|back-end|blockchain|"
    r"infrastructure|platform|wallet|defi|smart-contract|solidity|rust)",
    re.I,
)


def _plain_text(value):
    value = unescape(value or "")
    value = re.sub(
        r"<\s*(?:br|p|div|li|h[1-6])\b[^>]*>",
        "\n",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"<\s*/\s*(?:p|div|li|h[1-6])\s*>",
        "\n",
        value,
        flags=re.I,
    )
    value = re.sub(r"<[^>]+>", " ", value)
    value = unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n+ *", "\n", value)
    return value.strip()


def _jobposting(page):
    scripts = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        page,
        re.I | re.S,
    )
    for raw in scripts:
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            continue

        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return None


def _locations(data):
    result = []
    raw_locations = data.get("jobLocation")
    if isinstance(raw_locations, dict):
        raw_locations = [raw_locations]

    if isinstance(raw_locations, list):
        for item in raw_locations:
            if not isinstance(item, dict):
                continue
            address = item.get("address") or {}
            if isinstance(address, str):
                location = address.strip()
            elif isinstance(address, dict):
                pieces = []
                for key in (
                    "streetAddress",
                    "addressLocality",
                    "addressRegion",
                    "postalCode",
                    "addressCountry",
                ):
                    piece = address.get(key)
                    if isinstance(piece, dict):
                        piece = piece.get("name")
                    if piece:
                        piece = str(piece).strip()
                        if piece and piece not in pieces:
                            pieces.append(piece)
                location = ", ".join(pieces)
            else:
                location = ""

            if location and location not in result:
                result.append(location)

    requirements = data.get("applicantLocationRequirements")
    if not result:
        if isinstance(requirements, dict):
            requirements = [requirements]
        if isinstance(requirements, list):
            for requirement in requirements:
                if not isinstance(requirement, dict):
                    continue
                name = requirement.get("name")
                if name:
                    name = str(name).strip()
                    if name and name not in result:
                        result.append(name)

    return result


def _apply_url(page):
    anchors = re.finditer(
        r'<a\b([^>]*?)href=["\']([^"\']+)["\']([^>]*)>(.*?)</a>',
        page,
        re.I | re.S,
    )
    for match in anchors:
        attrs = match.group(1) + " " + match.group(3)
        label = _plain_text(match.group(4))
        href = unescape(match.group(2)).strip()
        is_apply = (
            "apply now" in label.lower()
            or "button-apply-now" in attrs.lower()
        )
        if is_apply and href.startswith(("http://", "https://")):
            return href
    return None


def crawl(ctx):
    root = ctx.fetch(SITE + "sitemap.xml")
    sitemap_urls = re.findall(
        r"<loc>\s*([^<]*sitemap_jobs[^<]*)\s*</loc>",
        root,
        re.I,
    )
    seen = set()
    candidates = []

    for sitemap_url in sitemap_urls[:1]:
        xml = ctx.fetch(unescape(sitemap_url.strip()))
        urls = re.findall(
            r"<loc>\s*([^<]+/jobs/[^<]+)\s*</loc>",
            xml,
            re.I,
        )
        for raw_url in urls:
            url = unescape(raw_url.strip())
            if url in seen:
                continue
            if urlparse(url).hostname != "jobs.framework.ventures":
                continue

            seen.add(url)
            slug = url.rsplit("/", 1)[-1]
            if _ROLE_WORDS.search(slug):
                candidates.append(url)

    # Getro publishes sitemap partitions newest-first. Use the newest partition
    # for the requested bounded window of recent engineering and protocol roles.
    for url in candidates[:4]:
        page = ctx.fetch(url)
        data = _jobposting(page)
        if not data:
            continue

        title = str(data.get("title") or "").strip()
        description = _plain_text(data.get("description"))
        organization = data.get("hiringOrganization") or {}
        company = str(organization.get("name") or "").strip()
        locations = _locations(data)

        if not title or not company or not description or not locations:
            continue

        id_match = re.search(r"/jobs/(\d+)", url)
        fields = {
            "title": title,
            "company": company,
            "location": locations[0],
            "locations": locations,
            "description": description,
        }

        if id_match:
            fields["external_id"] = id_match.group(1)
        if data.get("datePosted"):
            fields["posted_at"] = data["datePosted"]
        if data.get("validThrough"):
            fields["expires_at"] = data["validThrough"]

        employment = data.get("employmentType")
        if isinstance(employment, str) and employment.strip():
            fields["employment_type"] = employment.strip()
        elif isinstance(employment, list):
            values = [
                str(item).strip()
                for item in employment
                if str(item).strip()
            ]
            if values:
                fields["employment_type"] = values

        if data.get("jobLocationType") == "TELECOMMUTE":
            fields["workplace_type"] = "remote"

        apply_url = _apply_url(page)
        if apply_url:
            fields["apply_url"] = apply_url

        fields["url"] = url
        fields["source"] = SITE
        yield fields
