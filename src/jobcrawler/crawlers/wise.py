import html
import json
import re
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

SITE = "https://wise.jobs/engineering"
EXTRA_HOSTS = []

SITEMAP_URL = "https://wise.jobs/vacanciessitemap.xml"
COMPANY = "Wise"
SOURCE = "wise"
MAX_POSTINGS = 8

JID_RE = re.compile(r"-jid-(\d+)$")


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
    if isinstance(value, list):
        parts = [_text(v) for v in value]
        return ", ".join(p for p in parts if p) or None
    return str(value).strip() or None


def _find_jobposting(page):
    for raw in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        page,
        flags=re.I | re.S,
    ):
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
                if typ == "JobPosting" or (
                    isinstance(typ, list) and "JobPosting" in typ
                ):
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
    if not isinstance(loc, list):
        loc = [loc] if loc else []
    parts = []
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
                address.get("addressLocality"),
                address.get("addressRegion"),
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
    return "; ".join(dict.fromkeys(parts)) if parts else None


def _salary(job):
    benefits = job.get("jobBenefits")
    if isinstance(benefits, list):
        benefits = ", ".join(str(b).strip() for b in benefits if b)
    if isinstance(benefits, str) and benefits.strip():
        return benefits.strip()
    base = job.get("baseSalary")
    if isinstance(base, str) and base.strip():
        return base.strip()
    return None


def _external_id(url, job):
    match = JID_RE.search(urlparse(url).path)
    if match:
        return match.group(1)
    ident = job.get("identifier")
    if isinstance(ident, dict):
        value = ident.get("value")
        if value:
            return str(value).strip() or None
    return None


def _urls_from_sitemap(xml):
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
        if not JID_RE.search(urlparse(loc).path):
            continue
        lastmod_el = url_el.find("{*}lastmod")
        lastmod = (
            lastmod_el.text.strip()
            if lastmod_el is not None and lastmod_el.text
            else ""
        )
        items.append((lastmod, -idx, loc))
    items.sort(reverse=True)
    return [loc for _, __, loc in items]


def _clean_dict(data):
    return {k: v for k, v in data.items() if v is not None and v != ""}


def crawl(ctx):
    xml = ctx.fetch(SITEMAP_URL)
    for url in _urls_from_sitemap(xml)[:MAX_POSTINGS]:
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
        if not (title and description and location):
            continue
        out = {
            "url": _text(job.get("url")) or url,
            "title": title,
            "company": COMPANY,
            "location": _text(location),
            "description": description,
            "source": SOURCE,
            "external_id": _external_id(url, job),
            "salary": _salary(job),
            "employment_type": _text(job.get("employmentType")),
            "department": _text(job.get("industry")),
            "posted_at": _text(job.get("datePosted")),
            "expires_at": _text(job.get("validThrough")),
        }
        yield _clean_dict(out)
