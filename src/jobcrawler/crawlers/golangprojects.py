
import html
import json
import re

SITE = "https://www.golangprojects.com/golang-remote-jobs.html"
EXTRA_HOSTS = []

SITEMAP_URL = "https://www.golangprojects.com/sitemap.xml"
JOB_URL_RE = re.compile(r"https://www\.golangprojects\.com/golang-(?:go|remote)-job-[^<\s]+?\.html", re.I)


def _strip_tags(value):
    if value is None:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\r", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*", "\n\n", text)
    return text.strip() or None


def _first(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _jsonld_objects(page):
    for match in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page, re.I | re.S):
        raw = html.unescape(match.group(1)).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop(0)
            if isinstance(item, dict):
                graph = item.get("@graph")
                if isinstance(graph, list):
                    stack.extend(graph)
                yield item
            elif isinstance(item, list):
                stack.extend(item)


def _find_jobposting(page):
    for obj in _jsonld_objects(page):
        typ = obj.get("@type")
        types = typ if isinstance(typ, list) else [typ]
        if any(str(t).lower() == "jobposting" for t in types):
            return obj
    return {}


def _location_from_jsonld(job):
    loc = _first(job.get("jobLocation"))
    if not isinstance(loc, dict):
        return None
    addr = loc.get("address")
    parts = []
    if isinstance(addr, dict):
        for key in ("addressLocality", "addressRegion", "addressCountry"):
            val = addr.get(key)
            if val:
                parts.append(str(val))
    elif isinstance(addr, str):
        parts.append(addr)
    return ", ".join(parts) if parts else None


def _location_from_html(page):
    m = re.search(r'<h2\b.*?</h2>\s*(.*?)\s*(?:&nbsp;\s*)?<i[^>]*>\(Posted', page, re.I | re.S)
    if not m:
        return None
    names = re.findall(r'itemprop=["\']name["\'][^>]*>(.*?)</span>', m.group(1), re.I | re.S)
    # Breadcrumb names are country/region/city-ish; omit the title item if present.
    cleaned = [_strip_tags(x) for x in names]
    cleaned = [x for x in cleaned if x]
    if len(cleaned) >= 2:
        return ", ".join(cleaned[-2:])
    return ", ".join(cleaned) if cleaned else None


def _company_from_jsonld(job):
    org = job.get("hiringOrganization")
    if isinstance(org, dict):
        return org.get("name")
    if isinstance(org, str):
        return org
    return None


def _company_from_html(page):
    m = re.search(r"<h2\b[^>]*>.*?</span>\s*&nbsp;at\s*(.*?)</h2>", page, re.I | re.S)
    if m:
        return _strip_tags(m.group(1))
    m = re.search(r'<img[^>]+(?:alt|title)=["\']([^"\']+)["\'][^>]*class=["\'][^"\']*shadowbig', page, re.I | re.S)
    return _strip_tags(m.group(1)) if m else None


def _title_from_html(page):
    m = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.I | re.S)
    title = _strip_tags(m.group(1)) if m else None
    if title:
        title = re.sub(r"^\s*(?:Golang|Go)\s*/\s*Go Job:\s*", "", title, flags=re.I).strip()
    return title


def _description_from_html(page):
    m = re.search(r"<b>\s*Job description\s*</b><br>\s*(.*?)(?:<br>\s*&nbsp;\s*<br>\s*</div>|<div id=[\"']applydiv[\"']|<button class=[\"']cs-button|<style>)", page, re.I | re.S)
    return _strip_tags(m.group(1)) if m else None


def _apply_url(page, url):
    m = re.search(r'<a[^>]+class=["\'][^"\']*cs-button[^"\']*["\'][^>]+href=["\']([^"\']+)["\'][^>]*>\s*Apply now!', page, re.I | re.S)
    if m:
        return html.unescape(m.group(1)).strip()
    m = re.search(r'id=["\']applydivin["\'][^>]*>\s*Apply at this url:\s*(.*?)\s*</span>', page, re.I | re.S)
    if m:
        val = _strip_tags(m.group(1))
        if val:
            return val
    return url


def _posted_from_html(page):
    m = re.search(r"\(Posted\s+([A-Za-z]+)\s+(\d{1,2})\s+(\d{4})\)", page, re.I)
    if not m:
        return None
    months = {
        "jan": "01", "feb": "02", "mar": "03", "apr": "04",
        "may": "05", "jun": "06", "jul": "07", "aug": "08",
        "sep": "09", "oct": "10", "nov": "11", "dec": "12",
    }
    month = months.get(m.group(1)[:3].lower())
    if not month:
        return None
    return "%s-%s-%02d" % (m.group(3), month, int(m.group(2)))


def _salary(job):
    base = job.get("baseSalary")
    if not isinstance(base, dict):
        return None
    value = base.get("value")
    currency = base.get("currency")
    unit = None
    amount = None
    if isinstance(value, dict):
        unit = value.get("unitText")
        min_v = value.get("minValue")
        max_v = value.get("maxValue")
        val = value.get("value")
        if min_v is not None and max_v is not None:
            amount = "%s-%s" % (min_v, max_v)
        elif val is not None:
            amount = str(val)
        elif min_v is not None:
            amount = str(min_v)
        elif max_v is not None:
            amount = str(max_v)
    elif value is not None:
        amount = str(value)
    if not amount:
        return None
    bits = []
    if currency:
        bits.append(str(currency))
    bits.append(amount)
    if unit:
        bits.append(str(unit))
    return " ".join(bits)


def _parse_detail(ctx, url):
    page = ctx.fetch(url)
    job = _find_jobposting(page)

    item = {
        "url": url,
        "title": _strip_tags(job.get("title")) or _title_from_html(page),
        "company": _company_from_jsonld(job) or _company_from_html(page),
        "location": _location_from_jsonld(job) or _location_from_html(page),
        "description": _strip_tags(job.get("description")) or _description_from_html(page),
        "source": SITE,
    }

    mappings = [
        ("employment_type", "employmentType"),
        ("workplace_type", "jobLocationType"),
        ("posted_at", "datePosted"),
        ("expires_at", "validThrough"),
        ("external_id", "identifier"),
    ]
    for out_key, in_key in mappings:
        val = job.get(in_key)
        if isinstance(val, dict):
            val = val.get("value") or val.get("name")
        if isinstance(val, list):
            val = ", ".join(str(x) for x in val if x)
        if val:
            item[out_key] = str(val)

    if not item.get("posted_at"):
        posted = _posted_from_html(page)
        if posted:
            item["posted_at"] = posted

    apply_url = _apply_url(page, url)
    if apply_url:
        item["apply_url"] = apply_url

    salary = _salary(job)
    if salary:
        item["salary"] = salary

    if item.get("title") and item.get("company") and item.get("location") and item.get("description"):
        return {k: v for k, v in item.items() if v not in (None, "")}
    return None


def crawl(ctx):
    sitemap = ctx.fetch(SITEMAP_URL)
    seen = set()
    for url in JOB_URL_RE.findall(sitemap):
        url = html.unescape(url)
        if url in seen:
            continue
        seen.add(url)
        item = _parse_detail(ctx, url)
        if item:
            yield item
