
import json
import re
import html

SITE = "https://allstate.wd5.myworkdayjobs.com/wday/cxs/allstate/Allstate_Careers/jobs"
EXTRA_HOSTS = []

SITEMAP_URL = "https://allstate.wd5.myworkdayjobs.com/allstate_careers/siteMap.xml"

def _html_to_text(value):
    text = html.unescape(value or "")
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h1|h2|h3|h4|tr|ul|ol)\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*", "\n\n", text)
    return text.strip()

def _locs(xml):
    return re.findall(r"<loc>\s*([^<]+)\s*</loc>", xml or "")

def _jsonld_blocks(page):
    pattern = r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>'
    return re.findall(pattern, page or "", flags=re.I | re.S)

def _as_list(value):
    if isinstance(value, list):
        return value
    if value:
        return [value]
    return []

def _location_from_jsonld(data):
    values = []
    for loc in _as_list(data.get("jobLocation")):
        if isinstance(loc, dict):
            addr = loc.get("address")
            if isinstance(addr, dict):
                val = addr.get("addressLocality") or addr.get("addressRegion") or addr.get("addressCountry")
                if val and val not in values:
                    values.append(val)
            elif isinstance(addr, str) and addr not in values:
                values.append(addr)
    if not values and data.get("jobLocationType") == "TELECOMMUTE":
        req = data.get("applicantLocationRequirements")
        if isinstance(req, dict) and req.get("name"):
            values.append(req.get("name"))
    return values

def _first_jobposting(page):
    for block in _jsonld_blocks(page):
        try:
            data = json.loads(html.unescape(block))
        except Exception:
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return {}

def _company(data):
    org = data.get("hiringOrganization")
    if isinstance(org, dict) and org.get("name"):
        return org.get("name")
    return "Allstate"

def _external_id(data):
    ident = data.get("identifier")
    if isinstance(ident, dict):
        return ident.get("value")
    if isinstance(ident, str):
        return ident
    return None

def crawl(ctx):
    xml = ctx.fetch(SITEMAP_URL)
    seen = set()
    count = 0
    for url in _locs(xml):
        if url in seen:
            continue
        seen.add(url)
        page = ctx.fetch(url)
        data = _first_jobposting(page)
        title = data.get("title")
        description = _html_to_text(data.get("description"))
        locations = _location_from_jsonld(data)
        location = locations[0] if locations else None
        if not title or not description or not location:
            continue

        job = {
            "url": url,
            "title": title,
            "company": _company(data),
            "location": location,
            "description": description,
            "source": SITE,
        }
        if locations:
            job["locations"] = locations
        external_id = _external_id(data)
        if external_id:
            job["external_id"] = external_id
        if data.get("datePosted"):
            job["posted_at"] = data.get("datePosted")
        if data.get("validThrough"):
            job["expires_at"] = data.get("validThrough")
        if data.get("employmentType"):
            job["employment_type"] = data.get("employmentType")
        if data.get("jobLocationType"):
            job["workplace_type"] = data.get("jobLocationType")
        yield job
        count = count + 1
        if count >= 15:
            return
