
import json
import re
import html
from urllib.parse import urlsplit, urlunsplit

SITE = "https://crypto.jobs/"
EXTRA_HOSTS = []

try:
    Job
except NameError:
    Job = dict

FEED_URL = "https://crypto.jobs/feed/rss"

def _fetch(ctx, url):
    try:
        return ctx.fetch(url)
    except TypeError:
        return ctx.fetch(url, "GET", "")

def _clean_text(value):
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = html.unescape(value)
    value = re.sub(r"(?is)^<!\[CDATA\[(.*)\]\]>$", r"\1", value.strip())
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = value.replace("\r", "\n")
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None

def _canonical(url):
    if not url:
        return None
    parts = urlsplit(html.unescape(url.strip()))
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))

def _feed_urls(xml):
    urls = []
    seen = set()
    for item in re.findall(r"<item\b.*?</item>", xml, re.I | re.S):
        url = None
        guid = re.search(r"<guid\b[^>]*>\s*(.*?)\s*</guid>", item, re.I | re.S)
        link = re.search(r"<link\b[^>]*>\s*(.*?)\s*</link>", item, re.I | re.S)
        if guid:
            url = _canonical(_clean_text(guid.group(1)))
        elif link:
            url = _canonical(_clean_text(link.group(1)))
        if url and "://crypto.jobs/jobs/" in url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls

def _jsonld_objects(page):
    for raw in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page, re.I | re.S):
        raw = html.unescape(raw.strip())
        try:
            data = json.loads(raw)
        except Exception:
            continue
        if isinstance(data, list):
            for item in data:
                yield item
        else:
            yield data

def _find_jobposting(page):
    for obj in _jsonld_objects(page):
        if not isinstance(obj, dict):
            continue
        typ = obj.get("@type")
        if typ == "JobPosting" or (isinstance(typ, list) and "JobPosting" in typ):
            return obj
        graph = obj.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                if not isinstance(item, dict):
                    continue
                typ = item.get("@type")
                if typ == "JobPosting" or (isinstance(typ, list) and "JobPosting" in typ):
                    return item
    return None

def _org_name(org):
    if isinstance(org, dict):
        return _clean_text(org.get("name"))
    return _clean_text(org)

def _location(data):
    names = []
    req = data.get("applicantLocationRequirements")
    if isinstance(req, dict):
        names.append(req.get("name"))
    elif isinstance(req, list):
        for item in req:
            names.append(item.get("name") if isinstance(item, dict) else item)

    loc = data.get("jobLocation")
    locs = loc if isinstance(loc, list) else ([loc] if loc else [])
    for item in locs:
        if isinstance(item, dict):
            addr = item.get("address")
            if isinstance(addr, dict):
                names.append(addr.get("addressLocality") or addr.get("addressRegion") or addr.get("addressCountry"))
            else:
                names.append(item.get("name") or addr)
        else:
            names.append(item)

    cleaned = []
    for name in names:
        text = _clean_text(name)
        if text and text not in cleaned:
            cleaned.append(text)
    if cleaned:
        return ", ".join(cleaned)
    if data.get("jobLocationType") == "TELECOMMUTE":
        return "Remote"
    return None

def _salary(data):
    base = data.get("baseSalary")
    if not isinstance(base, dict):
        return None
    currency = _clean_text(base.get("currency"))
    value = base.get("value")
    unit = None
    amount = None
    if isinstance(value, dict):
        unit = _clean_text(value.get("unitText"))
        amount = value.get("value")
        if amount is None:
            minv = value.get("minValue")
            maxv = value.get("maxValue")
            if minv is not None and maxv is not None:
                amount = "%s-%s" % (minv, maxv)
            elif minv is not None:
                amount = minv
            elif maxv is not None:
                amount = maxv
    else:
        amount = value
    amount = _clean_text(amount)
    parts = [part for part in (currency, amount, unit) if part]
    return " ".join(parts) if parts else None

def _workplace_type(data):
    value = data.get("jobLocationType")
    if value == "TELECOMMUTE":
        return "Remote"
    return _clean_text(value)

def _external_id(url):
    path = urlsplit(url).path.rstrip("/")
    return path.rsplit("/", 1)[-1] if "/jobs/" in path else None

def _job_from_jsonld(data, fallback_url):
    url = _canonical(data.get("url") or fallback_url)
    item = {
        "url": url,
        "title": _clean_text(data.get("title")),
        "company": _org_name(data.get("hiringOrganization")),
        "location": _location(data),
        "description": _clean_text(data.get("description")),
        "source": SITE,
    }
    if not all(item.values()):
        return None

    optional = {
        "employment_type": _clean_text(data.get("employmentType")),
        "workplace_type": _workplace_type(data),
        "posted_at": _clean_text(data.get("datePosted")),
        "expires_at": _clean_text(data.get("validThrough")),
        "salary": _salary(data),
        "external_id": _external_id(url),
    }
    for key, value in optional.items():
        if value:
            item[key] = value
    return Job(**item) if Job is not dict else item

def crawl(ctx):
    feed = _fetch(ctx, FEED_URL)
    for url in _feed_urls(feed)[:8]:
        page = _fetch(ctx, url)
        data = _find_jobposting(page)
        if not data:
            continue
        job = _job_from_jsonld(data, url)
        if job:
            yield job
