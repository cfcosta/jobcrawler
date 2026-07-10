
import re
import json
import html

SITE = "https://accenture.wd103.myworkdayjobs.com/wday/cxs/accenture/AccentureCareers/jobs"
EXTRA_HOSTS = []


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = html.unescape(value)
        value = re.sub(r"<[^>]+>", " ", value)
        value = re.sub(r"\s+", " ", value).strip()
        return value or None
    return value


def _text_from_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|tr|h[1-6])\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None


def _job(fields):
    return {k: v for k, v in fields.items() if v is not None and v != ""}


def _jsonld_job(page):
    scripts = re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', page, flags=re.I | re.S)
    for script in scripts:
        try:
            data = json.loads(html.unescape(script).strip())
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop(0)
            if isinstance(item, list):
                stack = item + stack
            elif isinstance(item, dict):
                typ = item.get("@type")
                if typ == "JobPosting" or (isinstance(typ, list) and "JobPosting" in typ):
                    return item
                val = item.get("@graph")
                if isinstance(val, list):
                    stack = val + stack
                val = item.get("mainEntity")
                if isinstance(val, dict):
                    stack.insert(0, val)
                elif isinstance(val, list):
                    stack = val + stack
    return None


def _location(value):
    if isinstance(value, dict):
        addr = value.get("address")
        if isinstance(addr, dict):
            parts = [addr.get("addressLocality"), addr.get("addressRegion"), addr.get("addressCountry")]
            text = ", ".join([p for p in parts if p])
            if text:
                return text
        return _clean(value.get("name"))
    if isinstance(value, list) and value:
        return _location(value[0])
    return _clean(value)


def _identifier(value):
    if isinstance(value, dict):
        return _clean(value.get("value") or value.get("name"))
    return _clean(value)


def crawl(ctx):
    sitemap_url = "https://accenture.wd103.myworkdayjobs.com/AccentureCareers/siteMap.xml"
    xml = ctx.fetch(sitemap_url)
    locs = re.findall(r"<loc>(https://accenture\.wd103\.myworkdayjobs\.com/AccentureCareers/job/[^<]+)</loc>", xml)
    terms = ("/So-Paulo", "/Sao-Paulo", "/Rio-de-Janeiro", "/Belo-Horizonte", "/Brasilia", "/Curitiba", "/Recife", "/Nova-Lima", "/Barueri", "/Porto-Alegre")
    urls = [u for u in locs if any(t in u for t in terms)] or locs

    seen = set()
    for url in urls[:12]:
        if url in seen:
            continue
        seen.add(url)
        page = ctx.fetch(url)
        data = _jsonld_job(page)
        if not data:
            continue
        org = data.get("hiringOrganization") or {}
        company = _clean(org.get("name") if isinstance(org, dict) else org) or "Accenture"
        title = _clean(data.get("title"))
        description = _text_from_html(data.get("description"))
        location = _location(data.get("jobLocation"))
        posted_at = _clean(data.get("datePosted"))
        expires_at = _clean(data.get("validThrough"))
        external_id = _identifier(data.get("identifier"))
        employment_type = _clean(data.get("employmentType"))
        workplace_type = _clean(data.get("jobLocationType"))

        yield _job({
            "url": url,
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "posted_at": posted_at,
            "expires_at": expires_at,
            "external_id": external_id,
            "employment_type": employment_type,
            "workplace_type": workplace_type,
            "apply_url": url,
            "source": SITE,
        })
