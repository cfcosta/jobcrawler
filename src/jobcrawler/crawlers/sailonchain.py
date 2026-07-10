
import html
import json
import re
from urllib.parse import urlsplit

SITE = "https://sailonchain.com/"
EXTRA_HOSTS = []
MAX_JOBS = 25


def _clean_text(value):
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = html.unescape(value)
    value = re.sub(r"(?is)<br\s*/?>", "\n", value)
    value = re.sub(r"(?is)</p\s*>", "\n\n", value)
    value = re.sub(r"(?is)<[^>]+>", " ", value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _iter_jsonld_objects(page):
    scripts = re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page, re.I | re.S)
    for raw in scripts:
        raw = html.unescape(raw).strip()
        try:
            data = json.loads(raw)
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop(0)
            if isinstance(item, dict):
                yield item
                graph = item.get("@graph")
                if isinstance(graph, list):
                    stack.extend(graph)
            elif isinstance(item, list):
                stack.extend(item)


def _first_text(value):
    if isinstance(value, str):
        return _clean_text(value)
    if isinstance(value, dict):
        for key in ("name", "addressLocality", "addressRegion", "addressCountry"):
            text = _first_text(value.get(key))
            if text:
                return text
    return None


def _location(job):
    parts = []
    locs = job.get("jobLocation")
    if isinstance(locs, dict):
        locs = [locs]
    if isinstance(locs, list):
        for loc in locs:
            if not isinstance(loc, dict):
                continue
            address = loc.get("address")
            if isinstance(address, dict):
                vals = []
                for key in ("addressLocality", "addressRegion", "addressCountry"):
                    text = _first_text(address.get(key))
                    if text and text not in vals:
                        vals.append(text)
                if vals:
                    parts.append(", ".join(vals))
            text = _first_text(loc)
            if text and text not in parts:
                parts.append(text)
    req = job.get("applicantLocationRequirements")
    if isinstance(req, dict):
        text = _first_text(req)
        if text and text not in parts:
            parts.append(text)
    elif isinstance(req, list):
        for entry in req:
            text = _first_text(entry)
            if text and text not in parts:
                parts.append(text)
    if job.get("jobLocationType") and not parts:
        parts.append(str(job.get("jobLocationType")))
    return "; ".join(parts) if parts else None


def _company(job):
    org = job.get("hiringOrganization")
    if isinstance(org, dict):
        return _clean_text(org.get("name"))
    return _clean_text(org)


def _external_id(job):
    ident = job.get("identifier")
    if isinstance(ident, dict):
        return _clean_text(ident.get("value") or ident.get("name"))
    return _clean_text(ident)


def _salary(job):
    salary = job.get("baseSalary")
    if not isinstance(salary, dict):
        return None
    currency = salary.get("currency")
    value = salary.get("value")
    if isinstance(value, dict):
        minv = value.get("minValue")
        maxv = value.get("maxValue")
        unit = value.get("unitText")
        val = value.get("value")
        pieces = []
        if minv is not None and maxv is not None:
            pieces.append("%s-%s" % (minv, maxv))
        elif minv is not None:
            pieces.append("%s+" % minv)
        elif maxv is not None:
            pieces.append("up to %s" % maxv)
        elif val is not None:
            pieces.append(str(val))
        if currency:
            pieces.append(str(currency))
        if unit:
            pieces.append(str(unit))
        return " ".join(pieces) or None
    if value is not None:
        pieces = [str(value)]
        if currency:
            pieces.append(str(currency))
        return " ".join(pieces)
    return None


def _jobposting_from(page):
    for obj in _iter_jsonld_objects(page):
        typ = obj.get("@type")
        if typ == "JobPosting" or (isinstance(typ, list) and "JobPosting" in typ):
            return obj
    return None


def _apply_url(page):
    """The "Visit job opening page" CTA's external target, or None.

    Job pages render the CTA as ``<a class="job-outbound-cta ..." href="...">``
    (twice — top and bottom, same href). The href is the company's real posting
    on its own ATS. None when the anchor is absent or points back at
    sailonchain.com, so the caller omits ``apply_url`` and a previously stored
    value is never clobbered by a transient miss.
    """
    tag = re.search(
        r'<a\b[^>]*\bclass=["\'][^"\']*\bjob-outbound-cta\b[^"\']*["\'][^>]*>',
        page,
        re.IGNORECASE,
    )
    if not tag:
        return None
    href = re.search(r'\bhref=["\']([^"\']+)["\']', tag.group(0), re.IGNORECASE)
    if not href:
        return None
    url = html.unescape(href.group(1)).strip()
    if not url.lower().startswith(("http://", "https://")):
        return None
    # Host check only — the CTA tags its target with ?ref=sailonchain.com.
    host = urlsplit(url).netloc.lower()
    if host == "sailonchain.com" or host.endswith(".sailonchain.com"):
        return None
    return url


def crawl(ctx):
    sitemap = ctx.fetch("https://sailonchain.com/sitemaps/jobs.xml")
    urls = re.findall(r"<loc>\s*([^<]+?)\s*</loc>", sitemap)
    seen = set()
    yielded = 0

    for raw_url in urls:
        if yielded >= MAX_JOBS:
            break
        url = html.unescape(raw_url.strip())
        if not url or url in seen:
            continue
        seen.add(url)

        page = ctx.fetch(url)
        data = _jobposting_from(page)
        if not data:
            continue

        title = _clean_text(data.get("title"))
        description = _clean_text(data.get("description"))
        company = _company(data)
        location = _location(data)
        if not (title and description and company and location):
            continue

        job = {
            "url": _clean_text(data.get("url")) or url,
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "source": SITE,
        }

        optional = {
            "apply_url": _apply_url(page),
            "employment_type": _clean_text(data.get("employmentType")),
            "workplace_type": _clean_text(data.get("jobLocationType")),
            "department": _clean_text(data.get("occupationalCategory") or data.get("industry")),
            "posted_at": _clean_text(data.get("datePosted")),
            "expires_at": _clean_text(data.get("validThrough")),
            "external_id": _external_id(data),
            "salary": _salary(data),
        }
        for key, value in optional.items():
            if value:
                job[key] = value

        yielded += 1
        yield job
