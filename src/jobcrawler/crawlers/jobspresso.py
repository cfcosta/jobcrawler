
import json
import re
import html

SITE = "https://jobspresso.co/jm-ajax/get_listings/?per_page=20&orderby=date&order=DESC&page=1&filter_job_type%5B%5D=developer"
EXTRA_HOSTS = []

SITEMAP_URL = "https://jobspresso.co/job_listing-sitemap4.xml"
MAX_JOBS = 25

def _strip_tags(value):
    if value is None:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None

def _first_string(value):
    if isinstance(value, str):
        return html.unescape(value).strip() or None
    if isinstance(value, list):
        for item in value:
            got = _first_string(item)
            if got:
                return got
    if isinstance(value, dict):
        for key in ("name", "value", "streetAddress", "addressLocality", "addressRegion", "addressCountry"):
            got = _first_string(value.get(key))
            if got:
                return got
    return None

def _location_from_obj(loc):
    if isinstance(loc, dict):
        address = loc.get("address")
        if isinstance(address, dict):
            parts = []
            for key in ("streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry"):
                val = _first_string(address.get(key))
                if val:
                    parts.append(val)
            if parts:
                return ", ".join(parts)
        got = _first_string(address)
        if got:
            return got
        return _first_string(loc)
    return _first_string(loc)

def _locations(job):
    loc = job.get("jobLocation")
    raw_locations = loc if isinstance(loc, list) else [loc]
    values = []
    for item in raw_locations:
        got = _location_from_obj(item)
        if got and got not in values:
            values.append(got)
    return values

def _jsonld_objects(page):
    scripts = re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page, re.I | re.S)
    for raw in scripts:
        raw = html.unescape(raw).strip()
        try:
            data = json.loads(raw)
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop(0)
            if isinstance(item, list):
                stack[:0] = item
            elif isinstance(item, dict):
                if isinstance(item.get("@graph"), list):
                    stack[:0] = item["@graph"]
                typ = item.get("@type")
                types = typ if isinstance(typ, list) else [typ]
                if "JobPosting" in types:
                    yield item

def _job_urls(sitemap):
    return re.findall(r"<loc>\s*(https://jobspresso\.co/job/[^<]+?)\s*</loc>", sitemap)

def crawl(ctx):
    sitemap = ctx.fetch(SITEMAP_URL)
    yielded = 0
    for url in _job_urls(sitemap):
        if yielded >= MAX_JOBS:
            return
        page = ctx.fetch(url)
        for data in _jsonld_objects(page):
            title = _first_string(data.get("title"))
            description = _strip_tags(data.get("description"))
            locations = _locations(data)
            if not title or not description or not locations:
                continue
            org = data.get("hiringOrganization") or {}
            company = _first_string(org.get("name") if isinstance(org, dict) else org) or "Jobspresso"
            job = {
                "url": url,
                "title": title,
                "company": company,
                "location": locations[0],
                "description": description,
                "source": "Jobspresso",
            }
            if len(locations) > 1:
                job["locations"] = locations
            date_posted = _first_string(data.get("datePosted"))
            if date_posted:
                job["posted_at"] = date_posted
            valid_through = _first_string(data.get("validThrough"))
            if valid_through:
                job["expires_at"] = valid_through
            employment_type = _first_string(data.get("employmentType"))
            if employment_type:
                job["employment_type"] = employment_type
            identifier = data.get("identifier")
            if isinstance(identifier, dict):
                identifier = identifier.get("value") or identifier.get("name")
            identifier = _first_string(identifier)
            if identifier:
                job["external_id"] = identifier
            apply_url = _first_string(data.get("url")) or url
            if apply_url:
                job["apply_url"] = apply_url
            yielded += 1
            yield job
            break
