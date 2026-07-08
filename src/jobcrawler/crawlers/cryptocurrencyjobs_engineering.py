
import re
import html

SITE = "https://cryptocurrencyjobs.co/engineering/"
EXTRA_HOSTS = []

SITEMAP_URL = "https://cryptocurrencyjobs.co/sitemap-jobs.xml"
MAX_JOBS = 20

def _strip_tags(value):
    value = html.unescape(value or "")
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|ul|ol|section|article)\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s*", "\n", value)
    return value.strip()

def _clean(value):
    if value is None:
        return None
    value = html.unescape(value)
    value = value.replace('\\"', '"').replace("\\/", "/")
    value = re.sub(r"\s+", " ", value).strip()
    return value or None

def _jsonld_script(page):
    match = re.search(r'<script\b(?=[^>]*application/ld\+json)[^>]*>(.*?)</script>', page, re.I | re.S)
    return html.unescape(match.group(1).strip()) if match else ""

def _scalar(raw, key):
    # The site's JSON-LD contains literal newlines inside string values, so use a tolerant field matcher.
    match = re.search(r'"' + re.escape(key) + r'"\s*:\s*"(.*?)"\s*(?=,\s*"|})', raw, re.S)
    return _clean(match.group(1)) if match else None

def _description(raw, page):
    desc = _scalar(raw, "description")
    if desc:
        text = _strip_tags(desc)
        if text:
            return text
    match = re.search(r'<div[^>]+class="[^"]*\bprose\b[^"]*"[^>]*>(.*?)<div class="mt-14', page, re.I | re.S)
    return _strip_tags(match.group(1)) if match else None

def _company(raw, page):
    match = re.search(r'"hiringOrganization"\s*:\s*\{.*?"name"\s*:\s*"(.*?)"', raw, re.S)
    if match:
        return _clean(match.group(1))
    match = re.search(r"<title>(.*?)</title>", page, re.I | re.S)
    if match:
        text = _strip_tags(match.group(1))
        company_match = re.search(r"\bat\s+(.+?)\s+-\s+Cryptocurrency Jobs$", text)
        if company_match:
            return company_match.group(1).strip()
    return None

def _employment_type(raw):
    match = re.search(r'"employmentType"\s*:\s*\[(.*?)\]', raw, re.S)
    if match:
        vals = [_clean(v) for v in re.findall(r'"([^"]+)"', match.group(1))]
        vals = [v for v in vals if v]
        if vals:
            return ", ".join(vals)
    return _scalar(raw, "employmentType")

def _sidebar_value(page, label):
    match = re.search(
        r'<h3[^>]*>\s*' + re.escape(label) + r'\s*</h3>\s*<ul[^>]*>(.*?)</ul>',
        page,
        re.I | re.S,
    )
    value = _strip_tags(match.group(1)) if match else None
    return value or None

def _apply_url(page):
    match = re.search(r'<a\b[^>]*href=(["\']?)([^"\'\s>]+)\1[^>]*>\s*Apply now\s*</a>', page, re.I | re.S)
    if not match:
        return None
    url = html.unescape(match.group(2)).strip()
    return url or None

def _workplace_type(raw, location):
    if re.search(r'"jobLocationType"\s*:\s*"TELECOMMUTE"', raw) or (location and "remote" in location.lower()):
        return "Remote"
    return None

def crawl(ctx):
    sitemap = ctx.fetch(SITEMAP_URL)
    urls = re.findall(r"<loc>\s*(https://cryptocurrencyjobs\.co/engineering/[^<]+/)\s*</loc>", sitemap)
    seen = set()
    yielded = 0

    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        page = ctx.fetch(url)
        raw = _jsonld_script(page)

        title = _scalar(raw, "title")
        description = _description(raw, page)
        company = _company(raw, page)
        location = _sidebar_value(page, "Location")
        if not location and re.search(r'"jobLocationType"\s*:\s*"TELECOMMUTE"', raw):
            location = "Remote"

        if not (title and company and location and description):
            continue

        job = {
            "url": url,
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "source": SITE,
        }

        posted_at = _scalar(raw, "datePosted")
        if posted_at:
            job["posted_at"] = posted_at

        expires_at = _scalar(raw, "validThrough")
        if expires_at:
            job["expires_at"] = expires_at

        employment_type = _employment_type(raw) or _sidebar_value(page, "Job type")
        if employment_type:
            job["employment_type"] = employment_type

        workplace_type = _workplace_type(raw, location)
        if workplace_type:
            job["workplace_type"] = workplace_type

        department = _sidebar_value(page, "Category")
        if department:
            job["department"] = department

        apply_url = _apply_url(page)
        if apply_url:
            job["apply_url"] = apply_url

        external_id = url.rstrip("/").rsplit("/", 1)[-1]
        if external_id:
            job["external_id"] = external_id

        yield job
        yielded += 1
        if yielded >= MAX_JOBS:
            break
