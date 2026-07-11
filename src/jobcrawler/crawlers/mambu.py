
import json
import re
import html
from urllib.parse import urljoin

SITE = "https://careers-mambu.icims.com/jobs/search?ss=1&in_iframe=1"
EXTRA_HOSTS = []


def _strip_html(value):
    text = value or ""
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|li|h1|h2|h3|h4|div|ul|ol)\s*>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*", "\n\n", text)
    return text.strip()


def _first_match(pattern, text):
    match = re.search(pattern, text)
    return match.group(1) if match else None


def _fetch(ctx, url):
    try:
        return ctx.fetch(url)
    except TypeError:
        return ctx.fetch(url, "GET", "")


def _json_ld_jobposting(page):
    for raw in re.findall(r'(?is)<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page):
        try:
            data = json.loads(html.unescape(raw.strip()))
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return {}


def _location_from_jsonld(data):
    locs = data.get("jobLocation")
    if isinstance(locs, dict):
        locs = [locs]
    values = []
    for loc in locs or []:
        if not isinstance(loc, dict):
            continue
        address = loc.get("address") if isinstance(loc.get("address"), dict) else {}
        locality = address.get("addressLocality")
        country = address.get("addressCountry")
        if country and locality and locality != "UNAVAILABLE":
            values.append(str(country) + "-" + str(locality))
        elif locality and locality != "UNAVAILABLE":
            values.append(str(locality))
        elif country and country != "UNAVAILABLE":
            values.append(str(country))
    return values


def _sitemap_urls(ctx):
    xml = _fetch(ctx, "https://careers-mambu.icims.com/sitemap.xml")
    urls = []
    seen = set()
    for loc in re.findall(r"<loc>\s*(.*?)\s*</loc>", xml):
        loc = html.unescape(loc.strip())
        if re.search(r"/jobs/\d+/.*/job/?$", loc) and loc not in seen:
            seen.add(loc)
            urls.append(loc)
    return urls


def crawl(ctx):
    for url in _sitemap_urls(ctx):
        page = _fetch(ctx, url + "?in_iframe=1")
        data = _json_ld_jobposting(page)
        if not data:
            continue

        title = data.get("title")
        description = _strip_html(data.get("description") or "")
        json_locations = _location_from_jsonld(data)
        location = json_locations[0] if json_locations else None
        job_id = _first_match(r"/jobs/(\d+)/", url)

        if not (title and description and location):
            continue

        job = {
            "url": url,
            "title": title,
            "company": "Mambu",
            "location": location,
            "description": description,
            "source": SITE,
        }
        if json_locations:
            job["locations"] = json_locations
        if data.get("employmentType"):
            job["employment_type"] = data.get("employmentType")
        if data.get("datePosted"):
            job["posted_at"] = data.get("datePosted")
        if data.get("validThrough"):
            job["expires_at"] = data.get("validThrough")
        if job_id:
            job["external_id"] = job_id

        apply_url = _first_match(r'(?is)<a\s+[^>]*href=["\']([^"\']*mode=apply[^"\']*)["\']', page)
        if apply_url:
            job["apply_url"] = urljoin(url, html.unescape(apply_url))

        yield job
