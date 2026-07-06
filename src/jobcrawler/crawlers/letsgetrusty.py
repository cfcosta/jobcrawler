
import json, re, html
from urllib.parse import urljoin

SITE = "https://jobs.letsgetrusty.com/"

def _jsonld_blocks(text):
    for raw in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', text, re.I | re.S):
        raw = html.unescape(raw.strip())
        try:
            yield json.loads(raw)
        except Exception:
            continue

def _find_jobposting(obj):
    if isinstance(obj, dict):
        typ = obj.get("@type")
        if typ == "JobPosting" or (isinstance(typ, list) and "JobPosting" in typ):
            return obj
        for v in obj.values():
            found = _find_jobposting(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_jobposting(v)
            if found:
                return found
    return None

def _itemlist_jobs(obj):
    jobs = []
    if isinstance(obj, dict):
        if obj.get("@type") == "ItemList":
            for el in obj.get("itemListElement") or []:
                item = el.get("item") if isinstance(el, dict) else None
                if isinstance(item, dict):
                    typ = item.get("@type")
                    if typ == "JobPosting" or (isinstance(typ, list) and "JobPosting" in typ):
                        jobs.append(item)
        for v in obj.values():
            jobs.extend(_itemlist_jobs(v))
    elif isinstance(obj, list):
        for v in obj:
            jobs.extend(_itemlist_jobs(v))
    return jobs

def _text(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = re.sub(r"<[^>]+>", " ", html.unescape(value))
        value = re.sub(r"[ \t\r\f\v]+", " ", value)
        value = re.sub(r"\n{3,}", "\n\n", value).strip()
        return value or None
    return str(value)

def _location(posting):
    locs = []
    def add_place(place):
        if not isinstance(place, dict):
            if isinstance(place, str) and place.strip():
                locs.append(place.strip())
            return
        addr = place.get("address")
        if isinstance(addr, dict):
            bits = [addr.get(k) for k in ("streetAddress", "addressLocality", "addressRegion", "addressCountry")]
            bits = [str(b).strip() for b in bits if b]
            if bits:
                locs.append(", ".join(bits))
        elif isinstance(addr, str) and addr.strip():
            locs.append(addr.strip())
        elif place.get("name"):
            locs.append(str(place.get("name")).strip())
    jl = posting.get("jobLocation")
    if isinstance(jl, list):
        for place in jl:
            add_place(place)
    else:
        add_place(jl)
    ar = posting.get("applicantLocationRequirements")
    if isinstance(ar, dict) and ar.get("name"):
        locs.append(str(ar.get("name")).strip())
    elif isinstance(ar, list):
        for item in ar:
            if isinstance(item, dict) and item.get("name"):
                locs.append(str(item.get("name")).strip())
    if posting.get("jobLocationType") == "TELECOMMUTE" and not locs:
        locs.append("Remote")
    seen = []
    for loc in locs:
        loc = _text(loc)
        if loc and loc not in seen:
            seen.append(loc)
    return "; ".join(seen) or "Remote"

def _org_name(posting):
    org = posting.get("hiringOrganization")
    if isinstance(org, dict):
        return _text(org.get("name"))
    return _text(org)

def _salary(posting):
    base = posting.get("baseSalary")
    if not isinstance(base, dict):
        return None
    val = base.get("value")
    currency = base.get("currency")
    unit = None
    amount = None
    if isinstance(val, dict):
        unit = val.get("unitText")
        minv = val.get("minValue")
        maxv = val.get("maxValue")
        single = val.get("value")
        if minv is not None and maxv is not None:
            amount = f"{minv}-{maxv}"
        elif minv is not None:
            amount = str(minv)
        elif maxv is not None:
            amount = str(maxv)
        elif single is not None:
            amount = str(single)
    elif val is not None:
        amount = str(val)
    parts = [p for p in (currency, amount, unit) if p]
    return " ".join(str(p) for p in parts) or None

def _clean_job(job):
    out = {
        "source": SITE,
    }
    if _text(job.get("url")):
        out["url"] = _text(job.get("url"))
    if _text(job.get("title")):
        out["title"] = _text(job.get("title"))
    if _text(job.get("description")):
        out["description"] = _text(job.get("description"))
    if _org_name(job):
        out["company"] = _org_name(job)
    out["location"] = _location(job)
    if _text(job.get("employmentType")):
        out["employment_type"] = _text(job.get("employmentType"))
    if _text(job.get("jobLocationType")):
        out["workplace_type"] = _text(job.get("jobLocationType"))
    if _text(job.get("datePosted")):
        out["posted_at"] = _text(job.get("datePosted"))
    if _text(job.get("validThrough")):
        out["expires_at"] = _text(job.get("validThrough"))
    if _salary(job):
        out["salary"] = _salary(job)
    return out

def crawl(ctx):
    seen = set()
    listing_urls = [SITE, SITE + "?page=1", SITE + "?page=2"]
    for listing_url in listing_urls:
        page = ctx.fetch(listing_url)
        for block in _jsonld_blocks(page):
            for item in _itemlist_jobs(block):
                url = item.get("url")
                if not url:
                    continue
                url = urljoin(SITE, url)
                if url in seen:
                    continue
                seen.add(url)
                detail = ctx.fetch(url)
                posting = None
                for detail_block in _jsonld_blocks(detail):
                    found = _find_jobposting(detail_block)
                    if found and (found.get("url") == item.get("url") or found.get("title") == item.get("title")):
                        posting = found
                if not posting:
                    posting = item
                data = _clean_job(posting)
                data["url"] = url
                if "company" not in data:
                    data["company"] = _org_name(item) or "Let's Get Rusty"
                m = re.search(r"[?&]job=([^&]+)", url)
                if m:
                    data["external_id"] = m.group(1)
                yield data
