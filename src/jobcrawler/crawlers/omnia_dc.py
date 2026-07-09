
import json
import re
import html as _html
from urllib.parse import urljoin

SITE = "https://omnia.gupy.io/"
EXTRA_HOSTS = []

def _strip_html(value):
    if not value:
        return None
    text = _html.unescape(str(value))
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h1|h2|h3|h4|tr)\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li(?:\s[^>]*)?>", "\n- ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None

def _fetch(ctx, url):
    try:
        return ctx.fetch(url)
    except TypeError:
        return ctx.fetch(url, "GET", "")

def _next_data(page):
    match = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', page, re.S | re.I)
    if not match:
        return {}
    return json.loads(_html.unescape(match.group(1)))

def _page_props(page):
    return _next_data(page).get("props", {}).get("pageProps", {})

def _location_from_listing(job):
    workplace = job.get("workplace") or {}
    address = workplace.get("address") or {}
    pieces = []
    for key in ("city", "stateShortName", "state", "country"):
        value = address.get(key)
        if value and value not in pieces:
            pieces.append(value)
    return ", ".join(pieces) if pieces else "Brasil"

def _location_from_detail(job, fallback):
    pieces = []
    for key in ("addressCity", "addressStateShortName", "addressState", "addressCountry"):
        value = job.get(key)
        if value and value not in pieces:
            pieces.append(value)
    return ", ".join(pieces) if pieces else fallback

def _body(job):
    sections = []
    for field in ("description", "responsibilities", "prerequisites", "relevantExperiences"):
        text = _strip_html(job.get(field))
        if text:
            sections.append(text)
    return "\n\n".join(sections).strip()

def _clean(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None

def crawl(ctx):
    root = _fetch(ctx, SITE)
    jobs = _page_props(root).get("jobs") or []

    for listing in jobs:
        job_id = listing.get("id")
        if not job_id:
            continue

        url = urljoin(SITE, "jobs/%s?jobBoardSource=gupy_public_page" % job_id)
        page = _fetch(ctx, url)
        job = _page_props(page).get("job") or {}
        if job.get("status") and job.get("status") != "published":
            continue

        title = _clean(job.get("name") or listing.get("title"))
        description = _body(job)
        if not title or not description:
            continue

        location_fallback = _location_from_listing(listing)
        item = {
            "url": url,
            "title": title,
            "company": "OMNIA",
            "location": _location_from_detail(job, location_fallback),
            "description": description,
            "source": SITE,
            "external_id": _clean(job.get("id") or listing.get("id")),
            "apply_url": url,
        }

        optional = {
            "employment_type": job.get("jobType") or listing.get("type"),
            "workplace_type": job.get("workplaceType") or (listing.get("workplace") or {}).get("workplaceType"),
            "department": job.get("department") or listing.get("department"),
            "posted_at": job.get("publishedAt"),
            "expires_at": job.get("expiresAt") or job.get("registerEndDate"),
        }
        for key, value in optional.items():
            value = _clean(value)
            if value:
                item[key] = value

        yield item
