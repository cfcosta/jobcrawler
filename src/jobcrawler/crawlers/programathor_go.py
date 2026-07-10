
import html
import json
import re
from urllib.parse import urljoin

SITE = "https://programathor.com.br/jobs-go"
EXTRA_HOSTS = []


def _clean_text(value):
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = html.unescape(value)
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|h[1-6]|li|ul|ol|section|article)\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s*", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = value.strip(" \n\t")
    return value or None


def _repair_json(raw):
    raw = html.unescape(raw).strip()
    out = []
    in_str = False
    esc = False
    for ch in raw:
        if in_str:
            if esc:
                out.append(ch)
                esc = False
            elif ch == "\\":
                out.append(ch)
                esc = True
            elif ch == '"':
                out.append(ch)
                in_str = False
            elif ch == "\n":
                out.append("\\n")
            elif ch == "\r":
                out.append("\\r")
            elif ch == "\t":
                out.append("\\t")
            elif ord(ch) < 32:
                out.append(" ")
            else:
                out.append(ch)
        else:
            out.append(ch)
            if ch == '"':
                in_str = True
    return "".join(out)


def _json_ld_objects(page):
    for match in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page, re.I | re.S):
        obj = None
        for candidate in (html.unescape(match.group(1)).strip(), _repair_json(match.group(1))):
            try:
                obj = json.loads(candidate)
                break
            except Exception:
                pass
        if isinstance(obj, list):
            for item in obj:
                yield item
        elif obj is not None:
            yield obj


def _jobposting(page):
    for obj in _json_ld_objects(page):
        if not isinstance(obj, dict):
            continue
        typ = obj.get("@type")
        if typ == "JobPosting" or (isinstance(typ, list) and "JobPosting" in typ):
            return obj
    return None


def _listing_links(page):
    seen = set()
    for href in re.findall(r'href=["\']([^"\']*/jobs/\d+[^"\']*)["\']', page, re.I):
        url = urljoin(SITE, html.unescape(href).split("#", 1)[0]).split("?", 1)[0]
        if re.match(r"https://programathor\.com\.br/jobs/\d+-", url) and url not in seen:
            seen.add(url)
            yield url


def _company_from_ld(ld):
    org = ld.get("hiringOrganization")
    if isinstance(org, dict):
        return _clean_text(org.get("name"))
    if isinstance(org, str):
        return _clean_text(org)
    ident = ld.get("identifier")
    if isinstance(ident, dict):
        return _clean_text(ident.get("name"))
    return None


def _address_to_text(job_location):
    if isinstance(job_location, list):
        values = [_address_to_text(x) for x in job_location]
        values = [v for v in values if v]
        return values[0] if values else None
    if not isinstance(job_location, dict):
        return None
    address = job_location.get("address")
    if isinstance(address, dict):
        street = _clean_text(address.get("streetAddress"))
        if street:
            return street
        parts = []
        for key in ("addressLocality", "addressRegion", "addressCountry"):
            part = _clean_text(address.get(key))
            if part and part != "-":
                parts.append(part)
        if parts:
            return ", ".join(parts)
    return _clean_text(job_location.get("name"))


def _detail_meta(page):
    flat = re.sub(r"\s+", " ", page)
    meta = {}
    m = re.search(r'<i class=["\'][^"\']*fa-globe[^"\']*["\']></i>\s*</span>\s*<a[^>]*>.*?</a>\s*([^<]+)</p>', flat, re.I)
    if m:
        meta["location"] = _clean_text(m.group(1))
    m = re.search(r'<i class=["\'][^"\']*money-bill[^"\']*["\']></i>\s*</span>\s*Sal[aá]rio:\s*([^<]+)</p>', flat, re.I)
    if m:
        meta["salary"] = _clean_text(m.group(1))
    return meta


def _main_description(page, fallback):
    m = re.search(r'<!--\s*Content job offer\s*-->(.*?)(?:<!--\s*End Content job offer\s*-->|<div class=["\']container footer|<footer\b)', page, re.I | re.S)
    if m:
        text = _clean_text(m.group(1))
        if text and len(text) > 80:
            return text
    return fallback


def _build_job(ctx, url):
    page = ctx.fetch(url)
    if re.search(r"(?i)(vaga n[aã]o encontrada|p[aá]gina n[aã]o encontrada)", page):
        return None

    ld = _jobposting(page) or {}
    meta = _detail_meta(page)

    title = _clean_text(ld.get("title"))
    if not title:
        mt = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
        title = _clean_text(mt.group(1)) if mt else None
        if title:
            title = re.sub(r"^\s*VAGA\s+", "", title, flags=re.I).strip()

    company = _company_from_ld(ld)
    location = meta.get("location") or _address_to_text(ld.get("jobLocation"))
    if not location and isinstance(ld.get("applicantLocationRequirements"), dict):
        location = _clean_text(ld["applicantLocationRequirements"].get("name"))
    if not location and re.search(r"(?i)Remoto", page):
        location = "Remoto"

    description = _main_description(page, _clean_text(ld.get("description")))
    if not (title and company and location and description):
        return None

    job = {
        "url": url,
        "title": title,
        "company": company,
        "location": location,
        "description": description,
        "source": SITE,
    }

    m = re.search(r"/jobs/(\d+)-", url)
    if m:
        job["external_id"] = m.group(1)

    for src, dst in (("datePosted", "posted_at"), ("validThrough", "expires_at"), ("employmentType", "employment_type")):
        val = _clean_text(ld.get(src))
        if val:
            job[dst] = val

    if meta.get("salary"):
        job["salary"] = meta["salary"]

    base = ld.get("baseSalary")
    if isinstance(base, dict):
        cur = _clean_text(base.get("currency"))
        val = base.get("value")
        if cur:
            job["salary_currency"] = cur
        if isinstance(val, dict):
            if val.get("minValue") is not None:
                job["salary_min"] = val.get("minValue")
            if val.get("maxValue") is not None:
                job["salary_max"] = val.get("maxValue")
            period = _clean_text(val.get("unitText"))
            if period:
                job["salary_period"] = period

    return job


def crawl(ctx):
    seen = set()
    yielded = 0

    # Programathor is a paginated HTML board; detail pages carry the full posting text.
    for page_no in range(1, 4):
        listing_url = SITE if page_no == 1 else SITE + "?page=" + str(page_no)
        listing = ctx.fetch(listing_url)
        links = [url for url in _listing_links(listing) if url not in seen]
        if not links:
            break

        for url in links:
            seen.add(url)
            job = _build_job(ctx, url)
            if job:
                yielded += 1
                yield job
            if yielded >= 30:
                return
