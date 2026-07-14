
import html
import json
import re
from urllib.parse import urljoin

SITE = "https://jobs.jobvite.com/oreilly-media"
EXTRA_HOSTS = []


def _clean_text(value):
    if value is None:
        return None
    value = html.unescape(re.sub(r"<[^>]*>", "", str(value)))
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def _listing_jobs(page):
    pattern = (
        r'<td[^>]*class=["\'][^"\']*\bjv-job-list-name\b[^"\']*["\'][^>]*>'
        r'\s*<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>\s*</td>'
        r'\s*<td[^>]*class=["\'][^"\']*\bjv-job-list-location\b[^"\']*["\'][^>]*>'
        r'(.*?)</td>'
    )
    jobs = []
    for href, title_html, location_html in re.findall(
        pattern, page, re.I | re.S
    ):
        title = _clean_text(title_html)
        location = _clean_text(location_html)
        if href and title and location:
            jobs.append({
                "url": urljoin(SITE, html.unescape(href)),
                "title": title,
                "location": location,
            })
    return jobs


def _jobposting(page):
    pattern = (
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>'
        r'(.*?)</script>'
    )
    for block in re.findall(pattern, page, re.I | re.S):
        repaired = block.strip().replace("\\'", "'")
        try:
            value = json.loads(repaired)
        except json.JSONDecodeError:
            continue

        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return None


def _salary(description):
    text = _clean_text(description)
    if not text:
        return None
    match = re.search(
        r"Salary Range\s*:\s*\$[\d,]+\s*-\s*\$[\d,]+",
        text,
        re.I,
    )
    return match.group(0) if match else None


def _apply_url(page, job_url):
    pattern = (
        r'<a\b[^>]*class=["\'][^"\']*\bjv-button-apply\b[^"\']*["\']'
        r'[^>]*href=["\']([^"\']+)["\']'
    )
    match = re.search(pattern, page, re.I)
    if not match:
        return None
    return urljoin(job_url, html.unescape(match.group(1)))


def crawl(ctx):
    listing_page = ctx.fetch(SITE)
    seen = set()

    for listing in _listing_jobs(listing_page):
        job_url = listing["url"]
        if job_url in seen:
            continue
        seen.add(job_url)

        page = ctx.fetch(job_url)
        data = _jobposting(page)
        if not data:
            continue

        title = _clean_text(data.get("title")) or listing["title"]
        description = _clean_text(data.get("description"))
        location = listing["location"]
        if not title or not description or not location:
            continue

        kwargs = {
            "url": job_url,
            "title": title,
            "company": "O'Reilly Media",
            "location": location,
            "description": description,
            "source": "Jobvite",
        }

        identifier = _clean_text(data.get("identifier"))
        if identifier:
            kwargs["external_id"] = identifier

        salary = _salary(data.get("description"))
        if salary:
            kwargs["salary"] = salary

        department = _clean_text(data.get("industry"))
        if department:
            kwargs["department"] = department

        posted_at = _clean_text(data.get("datePosted"))
        if posted_at:
            kwargs["posted_at"] = posted_at

        expires_at = _clean_text(data.get("validThrough"))
        if expires_at:
            kwargs["expires_at"] = expires_at

        apply_url = _apply_url(page, job_url)
        if apply_url:
            kwargs["apply_url"] = apply_url

        yield kwargs
