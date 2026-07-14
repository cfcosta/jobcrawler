
import html
import json
import re
from urllib.parse import urljoin, urlparse

SITE = "https://bito.freshteam.com/jobs"
EXTRA_HOSTS = []


def _plain_text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"<\s*(?:br|/p|/div|/li|/h[1-6]|/section|/article|/tr)\b[^>]*>",
        "\n",
        value,
        flags=re.I,
    )
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value).replace("\xa0", " ")

    lines = []
    for line in value.splitlines():
        clean = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        if clean:
            lines.append(clean)
    return "\n".join(lines)


def _job_links(page):
    links = []
    seen = set()
    site_host = urlparse(SITE).hostname

    for match in re.finditer(
        r'<a\b[^>]*\bhref\s*=\s*["\']([^"\']+)["\']',
        page,
        re.I,
    ):
        url = urljoin(SITE, html.unescape(match.group(1)))
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")

        if parsed.hostname != site_host:
            continue
        if not re.match(r"^/jobs/[^/]+/[^/]+$", path, re.I):
            continue

        canonical = parsed.scheme + "://" + parsed.netloc + path
        if canonical not in seen:
            seen.add(canonical)
            links.append(canonical)

    return links


def _json_ld_job(page):
    for match in re.finditer(
        r'<script\b([^>]*)>(.*?)</script\s*>',
        page,
        re.I | re.S,
    ):
        if "ld+json" not in match.group(1).lower():
            continue

        try:
            value = json.loads(match.group(2).strip())
        except (TypeError, ValueError):
            continue

        candidates = value if isinstance(value, list) else [value]
        index = 0
        while index < len(candidates):
            candidate = candidates[index]
            index += 1
            if not isinstance(candidate, dict):
                continue

            graph = candidate.get("@graph")
            if isinstance(graph, list):
                candidates.extend(graph)

            kind = candidate.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if "JobPosting" in kinds:
                return candidate

    return None


def _header_metadata(page):
    header_match = re.search(
        r'<div\b[^>]*class=["\'][^"\']*job-details-header[^"\']*["\'][^>]*>'
        r'(.*?)'
        r'<div\b[^>]*class=["\'][^"\']*job-details-content[^"\']*["\']',
        page,
        re.I | re.S,
    )
    header = header_match.group(1) if header_match else ""

    department = None
    department_match = re.search(
        r'<a\b[^>]*class=["\'][^"\']*link-back[^"\']*["\'][^>]*>(.*?)</a\s*>',
        header,
        re.I | re.S,
    )
    if department_match:
        department = _plain_text(department_match.group(1))

    locations = []
    location = None
    location_match = re.search(
        r'Preferable\s+Location\(s\)\s*:\s*(.*?)'
        r'<div\b[^>]*>\s*Work\s+Type\s*:',
        header,
        re.I | re.S,
    )
    if location_match:
        location = _plain_text(location_match.group(1))
        locations = [item.strip() for item in location.split("|") if item.strip()]

    work_type = None
    work_match = re.search(
        r'Work\s+Type\s*:\s*(.*?)</div\s*>',
        header,
        re.I | re.S,
    )
    if work_match:
        work_type = _plain_text(work_match.group(1))

    return department, location, locations, work_type


def _json_location(posting):
    location = posting.get("jobLocation")
    if isinstance(location, list):
        location = location[0] if location else None
    if not isinstance(location, dict):
        return None

    address = location.get("address")
    if isinstance(address, str):
        return address.strip() or None
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
        clean = str(value).strip() if value is not None else ""
        if clean and clean not in parts:
            parts.append(clean)
    return ", ".join(parts) or None



def _normalized_date(value):
    if not value:
        return None
    text = str(value).strip()
    match = re.match(
        r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})\s+UTC$",
        text,
    )
    if match:
        return match.group(1) + "T" + match.group(2) + "Z"
    return text

def crawl(ctx):
    board = ctx.fetch(SITE)

    for url in _job_links(board):
        page = ctx.fetch(url)
        posting = _json_ld_job(page)
        if not posting:
            continue

        title = _plain_text(str(posting.get("title") or ""))
        description = _plain_text(str(posting.get("description") or ""))
        if not title or not description:
            continue

        department, location, locations, work_type = _header_metadata(page)
        if not location:
            location = _json_location(posting)
        if not location and posting.get("remote") in (True, "true", "True"):
            location = "Remote"
        if not location:
            continue

        organization = posting.get("hiringOrganization")
        company = "Bito"
        if isinstance(organization, dict) and organization.get("name"):
            company = _plain_text(str(organization["name"])) or company

        job = {
            "url": url,
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "source": SITE,
        }

        if locations:
            job["locations"] = locations
        if department:
            job["department"] = department

        employment_type = posting.get("employmentType") or work_type
        if employment_type:
            job["employment_type"] = str(employment_type)

        if posting.get("remote") in (True, "true", "True"):
            job["workplace_type"] = "remote"

        if posting.get("datePosted"):
            job["posted_at"] = _normalized_date(posting["datePosted"])
        if posting.get("validThrough"):
            job["expires_at"] = _normalized_date(posting["validThrough"])

        path_parts = [part for part in urlparse(url).path.split("/") if part]
        if len(path_parts) >= 2:
            job["external_id"] = path_parts[1]

        if 'id="applicant-form"' in page or "id='applicant-form'" in page:
            job["apply_url"] = url + "#applicant-form"

        yield job
