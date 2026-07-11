
import html
import json
import re

SITE = "https://blackstone.wd1.myworkdayjobs.com/wday/cxs/blackstone/Blackstone_Careers/jobs"
EXTRA_HOSTS = []

SITEMAP_URL = "https://blackstone.wd1.myworkdayjobs.com/Blackstone_Careers/siteMap.xml"
COMPANY = "Blackstone"
MAX_JOBS = 12


def _clean_text(value):
    if not value:
        return None
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|tr)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return text.strip() or None


def _sitemap_urls(xml):
    urls = []
    seen = set()
    for raw in re.findall(r"<loc>\s*(.*?)\s*</loc>", xml):
        url = html.unescape(raw.strip())
        if "/Blackstone_Careers/job/" not in url:
            continue
        if "/en-US/" in url or "/ja-JP/" in url or "/zh-CN/" in url:
            continue
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _jsonld_objects(html_text):
    for script in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html_text, flags=re.I | re.S):
        text = html.unescape(script.strip())
        try:
            data = json.loads(text)
        except Exception:
            continue
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    yield item
        elif isinstance(data, dict):
            yield data


def _jobposting(html_text):
    for obj in _jsonld_objects(html_text):
        typ = obj.get("@type")
        if typ == "JobPosting" or (isinstance(typ, list) and "JobPosting" in typ):
            return obj
    return {}


def _location(job_json):
    loc = job_json.get("jobLocation")
    if isinstance(loc, list) and loc:
        loc = loc[0]
    if isinstance(loc, dict):
        addr = loc.get("address")
        if isinstance(addr, dict):
            return addr.get("addressLocality") or addr.get("addressRegion") or addr.get("addressCountry")
        if isinstance(addr, str):
            return addr
        return loc.get("name")
    if isinstance(loc, str):
        return loc
    return None


def crawl(ctx):
    xml = ctx.fetch(SITEMAP_URL)
    yielded = 0

    for url in _sitemap_urls(xml):
        if yielded >= MAX_JOBS:
            break

        page = ctx.fetch(url)
        data = _jobposting(page)
        if not data:
            continue

        title = data.get("title")
        description = _clean_text(data.get("description"))
        location = _location(data)

        if not title or not description or not location:
            continue

        job = {
            "url": url,
            "title": title,
            "company": COMPANY,
            "location": location,
            "description": description,
            "source": SITE,
        }

        identifier = data.get("identifier")
        if isinstance(identifier, dict) and identifier.get("value"):
            job["external_id"] = identifier["value"]
        if data.get("employmentType"):
            job["employment_type"] = data["employmentType"]
        if data.get("datePosted"):
            job["posted_at"] = data["datePosted"]

        yielded += 1
        yield job
