
import re
import json
import html
from urllib.parse import urljoin, urlparse

SITE = "https://golang.cafe/"
EXTRA_HOSTS = []

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

def _fetch(ctx, url):
    return ctx.fetch(url, headers=HEADERS)

def _strip_tags(s):
    if not s:
        return None
    s = re.sub(r"(?is)<script\b.*?</script>|<style\b.*?</style>|<noscript\b.*?</noscript>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(p|div|li|h[1-6]|section|article|tr)>", "\n", s)
    s = re.sub(r"(?is)<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n\s+", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip() or None

def _abs(url, base=SITE):
    u = html.unescape(url or "").strip()
    if not u or u.startswith(("#", "mailto:", "tel:", "javascript:")):
        return None
    full = urljoin(base, u)
    p = urlparse(full)
    if p.scheme not in ("http", "https") or p.netloc != urlparse(SITE).netloc:
        return None
    return full.split("#", 1)[0]

def _links(page, base):
    out = []
    for m in re.finditer(r'''(?is)<a\b[^>]*\bhref=["']([^"']+)["'][^>]*>''', page or ""):
        u = _abs(m.group(1), base)
        if u:
            out.append(u)
    return out

def _jsonld_jobs(page):
    jobs = []
    for m in re.finditer(r'''(?is)<script[^>]+type=["']application/ld\+json["'][^>]*>(.*?)</script>''', page or ""):
        raw = html.unescape(m.group(1)).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            obj = stack.pop(0)
            if isinstance(obj, list):
                stack[:0] = obj
                continue
            if not isinstance(obj, dict):
                continue
            typ = obj.get("@type") or obj.get("type")
            types = typ if isinstance(typ, list) else [typ]
            if any(str(t).lower() == "jobposting" for t in types):
                jobs.append(obj)
            graph = obj.get("@graph")
            if isinstance(graph, list):
                stack[:0] = graph
    return jobs

def _first_text(*vals):
    for v in vals:
        if isinstance(v, str):
            t = _strip_tags(v)
            if t:
                return t
        elif isinstance(v, dict):
            t = _first_text(v.get("name"), v.get("title"))
            if t:
                return t
    return None

def _location(value):
    if isinstance(value, str):
        return _strip_tags(value)
    if isinstance(value, list):
        parts = [_location(v) for v in value]
        return ", ".join([p for p in parts if p]) or None
    if isinstance(value, dict):
        addr = value.get("address")
        if isinstance(addr, dict):
            parts = [addr.get("addressLocality"), addr.get("addressRegion"), addr.get("addressCountry")]
            clean = []
            for p in parts:
                t = _strip_tags(p)
                if t:
                    clean.append(t)
            txt = ", ".join(clean)
            if txt:
                return txt
        return _first_text(value.get("name"), value.get("addressLocality"))
    return None

def _meta(page, name):
    patterns = [
        r'''(?is)<meta[^>]+property=["']%s["'][^>]+content=["']([^"']+)["']''' % re.escape(name),
        r'''(?is)<meta[^>]+name=["']%s["'][^>]+content=["']([^"']+)["']''' % re.escape(name),
        r'''(?is)<meta[^>]+content=["']([^"']+)["'][^>]+property=["']%s["']''' % re.escape(name),
        r'''(?is)<meta[^>]+content=["']([^"']+)["'][^>]+name=["']%s["']''' % re.escape(name),
    ]
    for pat in patterns:
        m = re.search(pat, page or "")
        if m:
            return html.unescape(m.group(1)).strip()
    return None

def _title_from_html(page):
    for pat in [r"(?is)<h1[^>]*>(.*?)</h1>", r"(?is)<title[^>]*>(.*?)</title>"]:
        m = re.search(pat, page or "")
        if m:
            t = _strip_tags(m.group(1))
            if t:
                t = re.sub(r"\s+[-|]\s+Golang Cafe.*$", "", t, flags=re.I).strip()
                return t
    return None

def _company_from_text(title, text):
    if title:
        m = re.search(r"\bat\s+([A-Z][^\n|,-]{2,80})", title)
        if m:
            return m.group(1).strip()
    for pat in [r"(?im)^Company\s*:?\s*(.+)$", r"(?im)^Hiring company\s*:?\s*(.+)$"]:
        m = re.search(pat, text or "")
        if m:
            return m.group(1).strip()
    return "Golang Cafe"

def _salary(text):
    m = re.search(r"(?i)(?:salary|compensation|pay|range)\s*:?\s*([^\n]{0,120}(?:\$|€|£|USD|EUR|GBP)[^\n]{2,160})", text or "")
    if m:
        return m.group(1).strip(" -:\t")
    m = re.search(r"(?i)((?:\$|€|£)\s?\d[\d,]*(?:\s?-\s?(?:\$|€|£)?\s?\d[\d,]*)?(?:\s?(?:k|K))?(?:\s*/\s?(?:year|yr|hour|hr))?)", text or "")
    return m.group(1).strip() if m else None

def _posted_at(page, text):
    for pat in [
        r'''(?is)<time[^>]+datetime=["']([^"']+)["']''',
        r"(?im)^Posted\s*:?\s*([A-Z][a-z]+\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2})",
    ]:
        m = re.search(pat, page or text or "")
        if m:
            return m.group(1).strip()
    return None

def _clean_date(value):
    if not value:
        return None
    value = str(value).strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})(.*)$", value)
    if m:
        return "%s-%02d-%02d%s" % (m.group(1), int(m.group(2)), int(m.group(3)), m.group(4))
    return value

def _apply_url(page, base):
    for m in re.finditer(r'''(?is)<a\b([^>]*)href=["']([^"']+)["']([^>]*)>(.*?)</a>''', page or ""):
        label = _strip_tags((m.group(1) or "") + " " + (m.group(3) or "") + " " + (m.group(4) or ""))
        if label and re.search(r"(?i)\b(apply|application)\b", label):
            return urljoin(base, html.unescape(m.group(2)).strip())
    return None

def _detail(ctx, url):
    page = _fetch(ctx, url)
    json_jobs = _jsonld_jobs(page)
    if json_jobs:
        j = json_jobs[0]
        title = _first_text(j.get("title"))
        desc = _strip_tags(j.get("description"))
        company = _first_text(j.get("hiringOrganization")) or "Golang Cafe"
        loc = _location(j.get("jobLocation")) or _location(j.get("applicantLocationRequirements")) or "Remote"
        if title and desc:
            item = {
                "url": url,
                "title": title,
                "company": company,
                "location": loc,
                "description": desc,
                "source": SITE,
            }
            if j.get("datePosted"):
                item["posted_at"] = _clean_date(j.get("datePosted"))
            if j.get("validThrough"):
                item["expires_at"] = _clean_date(j.get("validThrough"))
            if j.get("employmentType"):
                item["employment_type"] = ", ".join(j.get("employmentType")) if isinstance(j.get("employmentType"), list) else j.get("employmentType")
            if j.get("jobLocationType"):
                item["workplace_type"] = j.get("jobLocationType")
            sal = _salary(desc)
            if sal:
                item["salary"] = sal
            au = _apply_url(page, url)
            if au:
                item["apply_url"] = au
            return item

    body = None
    for pat in [
        r"(?is)<article\b[^>]*>(.*?)</article>",
        r"(?is)<main\b[^>]*>(.*?)</main>",
        r"(?is)<body\b[^>]*>(.*?)</body>",
    ]:
        m = re.search(pat, page or "")
        if m:
            body = _strip_tags(m.group(1))
            if body and len(body) > 200:
                break
    title = _title_from_html(page) or _meta(page, "og:title")
    desc = body or _strip_tags(_meta(page, "description") or _meta(page, "og:description"))
    if not title or not desc or len(desc) < 120:
        return None
    loc = "Remote" if re.search(r"(?i)\bremote\b", desc + " " + title) else "Not specified"
    item = {
        "url": url,
        "title": title,
        "company": _company_from_text(title, desc),
        "location": loc,
        "description": desc,
        "source": SITE,
    }
    sal = _salary(desc)
    if sal:
        item["salary"] = sal
    dt = _posted_at(page, desc)
    if dt:
        item["posted_at"] = _clean_date(dt)
    au = _apply_url(page, url)
    if au:
        item["apply_url"] = au
    if re.search(r"(?i)\bremote\b", desc):
        item["workplace_type"] = "Remote"
    return item

def crawl(ctx):
    page = _fetch(ctx, SITE)
    candidates = []
    for u in _links(page, SITE):
        path = urlparse(u).path.lower()
        if any(x in path for x in ("/job", "/jobs", "/remote", "/golang")) and not any(x in path for x in ("/post", "/hire", "/pricing", "/login", "/signup")):
            candidates.append(u)
    out_seen = set()
    for url in candidates[:80]:
        if url in out_seen or url.rstrip("/") == SITE.rstrip("/"):
            continue
        out_seen.add(url)
        item = _detail(ctx, url)
        if item:
            yield item
