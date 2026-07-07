
import html
import json
import re
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

SITE = "https://cryptojobslist.com/sitemap-jobs.xml"
EXTRA_HOSTS = []

MAX_POSTINGS = 12

def _text(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = html.unescape(value)
        value = re.sub(r"<\s*br\s*/?\s*>", "\n", value, flags=re.I)
        value = re.sub(r"</\s*(p|div|li|h[1-6]|tr)\s*>", "\n", value, flags=re.I)
        value = re.sub(r"<[^>]+>", " ", value)
        value = html.unescape(value)
        value = re.sub(r"[ \t\r\f\v]+", " ", value)
        value = re.sub(r"\n\s*", "\n", value)
        return value.strip() or None
    return str(value).strip() or None

def _find_jobposting(page):
    for raw in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page, flags=re.I | re.S):
        raw = html.unescape(raw).strip()
        try:
            data = json.loads(raw)
        except Exception:
            continue
        stack = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                typ = item.get("@type")
                if typ == "JobPosting" or (isinstance(typ, list) and "JobPosting" in typ):
                    return item
                if isinstance(item.get("@graph"), list):
                    stack.extend(item["@graph"])
                for v in item.values():
                    if isinstance(v, (dict, list)):
                        stack.append(v)
            elif isinstance(item, list):
                stack.extend(item)
    return None

def _location(job):
    loc = job.get("jobLocation")
    parts = []
    if not isinstance(loc, list):
        loc = [loc] if loc else []
    for entry in loc:
        if isinstance(entry, str):
            val = entry.strip()
            if val:
                parts.append(val)
            continue
        if not isinstance(entry, dict):
            continue
        address = entry.get("address", entry)
        if isinstance(address, str):
            vals = [address]
        elif isinstance(address, dict):
            vals = [
                address.get("streetAddress"),
                address.get("addressLocality"),
                address.get("addressRegion"),
                address.get("postalCode"),
                address.get("addressCountry"),
            ]
        else:
            vals = []
        text = ", ".join(str(v).strip() for v in vals if v)
        if text:
            parts.append(text)
    remote = job.get("jobLocationType")
    if remote and str(remote).strip() not in parts:
        parts.append(str(remote).strip())
    return "; ".join(parts) if parts else None

def _salary(job):
    salary = job.get("baseSalary")
    if not salary:
        return None
    if isinstance(salary, str):
        return salary.strip() or None
    if not isinstance(salary, dict):
        return None
    currency = salary.get("currency")
    value = salary.get("value")
    if isinstance(value, dict):
        unit = value.get("unitText")
        min_value = value.get("minValue")
        max_value = value.get("maxValue")
        exact = value.get("value")
        pieces = []
        if currency:
            pieces.append(str(currency))
        if min_value is not None and max_value is not None and min_value != max_value:
            pieces.append("%s-%s" % (min_value, max_value))
        elif min_value is not None:
            pieces.append(str(min_value))
        elif exact is not None:
            pieces.append(str(exact))
        if unit:
            pieces.append(str(unit))
        return " ".join(pieces).strip() or None
    if value is not None:
        return ("%s %s" % (currency or "", value)).strip() or None
    return None

def _jobs_from_sitemap(xml):
    try:
        root = ET.fromstring(xml)
    except Exception:
        return []
    items = []
    for idx, url_el in enumerate(root.findall(".//{*}url")):
        loc_el = url_el.find("{*}loc")
        if loc_el is None or not loc_el.text:
            continue
        loc = loc_el.text.strip()
        if "/jobs/" not in loc:
            continue
        lastmod_el = url_el.find("{*}lastmod")
        lastmod = lastmod_el.text.strip() if lastmod_el is not None and lastmod_el.text else ""
        items.append((lastmod, -idx, loc))
    items.sort(reverse=True)
    return [loc for _, __, loc in items]

def _clean_dict(data):
    return {k: v for k, v in data.items() if v is not None and v != ""}

def crawl(ctx):
    xml = ctx.fetch(SITE)
    for url in _jobs_from_sitemap(xml)[:MAX_POSTINGS]:
        try:
            page = ctx.fetch(url)
        except Exception:
            continue
        job = _find_jobposting(page)
        if not job:
            continue
        title = _text(job.get("title"))
        description = _text(job.get("description"))
        location = _location(job)
        org = job.get("hiringOrganization") or {}
        company = org.get("name") if isinstance(org, dict) else None
        if not (title and description and location and company):
            continue
        employment = job.get("employmentType")
        if isinstance(employment, list):
            employment = ", ".join(str(x) for x in employment if x)
        out = {
            "url": job.get("url") or url,
            "title": title,
            "company": _text(company),
            "location": _text(location),
            "description": description,
            "salary": _salary(job),
            "employment_type": _text(employment),
            "workplace_type": _text(job.get("jobLocationType")),
            "department": _text(job.get("industry")),
            "posted_at": _text(job.get("datePosted")),
            "expires_at": _text(job.get("validThrough")),
            "external_id": urlparse(url).path.split("/jobs/", 1)[-1],
            "source": "CryptoJobsList",
        }
        yield _clean_dict(out)
