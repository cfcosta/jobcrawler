
import json
import re
from html import unescape
from urllib.parse import urljoin

from jobcrawler.runtime.contract import Job

SITE = "https://remoteok.com/api"
EXTRA_HOSTS = []

_BASE = "https://remoteok.com"
_PAGE_URL = (
    "https://remoteok.com/?action=get_jobs&premium=0"
    "&pagination=1&offset={offset}"
)


def _plain_text(value):
    if value is None:
        return None

    text = unescape(str(value))
    text = re.sub(
        r"(?i)<\s*(?:br|hr)\s*/?\s*>",
        "\n",
        text,
    )
    text = re.sub(
        r"(?i)</?\s*(?:address|article|aside|blockquote|dd|div|dl|dt|"
        r"figcaption|figure|footer|h[1-6]|header|li|main|nav|ol|p|pre|"
        r"section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text).replace("\xa0", " ")

    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line and (not lines or line != lines[-1]):
            lines.append(line)
    return "\n".join(lines).strip() or None


def _jobposting(chunk):
    scripts = re.findall(
        r"<script\b([^>]*)>(.*?)</script>",
        chunk,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for attrs, body in scripts:
        if "ld+json" not in attrs.lower():
            continue
        try:
            value = json.loads(body.strip())
        except (TypeError, ValueError):
            continue
        objects = value if isinstance(value, list) else [value]
        for obj in objects:
            if not isinstance(obj, dict):
                continue
            kind = obj.get("@type")
            if kind == "JobPosting" or (
                isinstance(kind, list) and "JobPosting" in kind
            ):
                return obj
    return None


def _address_text(place):
    if not isinstance(place, dict):
        return None

    if place.get("name"):
        return _plain_text(place["name"])

    address = place.get("address")
    if isinstance(address, str):
        return _plain_text(address)
    if not isinstance(address, dict):
        return None

    values = []
    for key in (
        "streetAddress",
        "addressLocality",
        "addressRegion",
        "postalCode",
        "addressCountry",
    ):
        value = address.get(key)
        if isinstance(value, dict):
            value = value.get("name")
        value = _plain_text(value)
        if value and value not in values:
            values.append(value)

    meaningful = [value for value in values if value.lower() != "anywhere"]
    if meaningful:
        values = meaningful
    return ", ".join(values) if values else None


def _locations(posting):
    raw_places = posting.get("jobLocation")
    if isinstance(raw_places, dict):
        raw_places = [raw_places]
    elif not isinstance(raw_places, list):
        raw_places = []

    locations = []
    for place in raw_places:
        value = _address_text(place)
        if value and value not in locations:
            locations.append(value)

    if not locations:
        requirements = posting.get("applicantLocationRequirements")
        if isinstance(requirements, dict):
            requirements = [requirements]
        if isinstance(requirements, list):
            for requirement in requirements:
                if isinstance(requirement, dict):
                    value = _plain_text(requirement.get("name"))
                else:
                    value = _plain_text(requirement)
                if value and value not in locations:
                    locations.append(value)

    if not locations and posting.get("jobLocationType") == "TELECOMMUTE":
        locations.append("Remote")

    return locations


def _fetch(ctx, url):
    try:
        return ctx.fetch(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                )
            },
        )
    except TypeError:
        return ctx.fetch(url)


def crawl(ctx):
    offset = 0
    seen_ids = set()

    while True:
        html = _fetch(ctx, _PAGE_URL.replace("{offset}", str(offset)))
        if not html:
            break

        starts = list(re.finditer(
            r'<tr\b'
            r'(?=[^>]*\bclass=["\'][^"\']*\bjob\b)'
            r'(?=[^>]*\bid=["\']job-(\d+)["\'])'
            r'[^>]*>',
            html,
            flags=re.IGNORECASE,
        ))
        if not starts:
            break

        next_offset = offset
        yielded_on_page = 0

        for index, match in enumerate(starts):
            end = starts[index + 1].start() if index + 1 < len(starts) else len(html)
            chunk = html[match.start():end]
            external_id = match.group(1)

            offset_match = re.search(
                r'\bdata-offset=["\'](\d+)["\']',
                match.group(0),
                flags=re.IGNORECASE,
            )
            if offset_match:
                next_offset = max(next_offset, int(offset_match.group(1)))

            if external_id in seen_ids:
                continue
            seen_ids.add(external_id)

            posting = _jobposting(chunk)
            if not posting:
                continue

            url_match = re.search(
                r'\bdata-url=["\']([^"\']+)["\']',
                match.group(0),
                flags=re.IGNORECASE,
            )
            if not url_match:
                url_match = re.search(
                    r'<a\b[^>]*\bhref=["\']([^"\']*/remote-jobs/[^"\']+)["\']',
                    chunk,
                    flags=re.IGNORECASE,
                )
            if not url_match:
                continue

            url = urljoin(_BASE, unescape(url_match.group(1)))
            title = _plain_text(posting.get("title"))
            description = _plain_text(posting.get("description"))

            organization = posting.get("hiringOrganization")
            company = None
            if isinstance(organization, dict):
                company = _plain_text(organization.get("name"))
            elif organization:
                company = _plain_text(organization)

            if not title or not company or not description:
                continue

            locations = _locations(posting)
            location = locations[0] if locations else "Remote"

            apply_match = re.search(
                r'<a\b[^>]*\bclass=["\'][^"\']*\baction-apply\b[^"\']*["\']'
                r'[^>]*\bhref=["\']([^"\']+)["\']',
                chunk,
                flags=re.IGNORECASE,
            )
            apply_url = (
                urljoin(_BASE, unescape(apply_match.group(1)))
                if apply_match else None
            )

            workplace_type = None
            if posting.get("jobLocationType") == "TELECOMMUTE":
                workplace_type = "remote"

            employment_type = posting.get("employmentType")
            if isinstance(employment_type, list):
                employment_type = ", ".join(
                    str(value) for value in employment_type if value
                ) or None
            elif employment_type is not None:
                employment_type = str(employment_type)

            kwargs = {
                "url": url,
                "source": SITE,
                "title": title,
                "company": company,
                "location": location,
                "description": description,
                "external_id": external_id,
                "posted_at": posting.get("datePosted") or None,
                "expires_at": posting.get("validThrough") or None,
                "employment_type": employment_type,
                "workplace_type": workplace_type,
                "apply_url": apply_url,
            }
            if locations:
                kwargs["locations"] = locations

            yield Job(**kwargs)
            yielded_on_page += 1

        if next_offset <= offset:
            break
        offset = next_offset

        # Rows can be malformed, but a page with no parseable postings should not
        # cause an unbounded crawl through an unexpected response.
        if yielded_on_page == 0:
            break
