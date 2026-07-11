
import json
import re
import html
from urllib.parse import urljoin, urlparse

SITE = "https://web3.career/"
EXTRA_HOSTS = []


def _fetch(ctx, url):
    try:
        return ctx.fetch(url)
    except TypeError:
        return ctx.fetch(url, method="GET", payload="")


def _clean_text(value):
    if value is None:
        return None
    text = str(value)
    text = html.unescape(html.unescape(text))
    text = re.sub(r'\s*(?:/?(?:br|p|div|span|ul|ol|li|strong|b|em|i|h[1-6])\s*/?>)\s*', ' ', text, flags=re.I)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = text.replace('\xa0', ' ')
    text = re.sub(r'\s+', ' ', text).strip()
    return text or None


def _normalize_date(value):
    text = _clean_text(value)
    if not text:
        return None
    # Convert Ruby-style offsets to ISO-8601, e.g. "2026-07-11 06:31:42 +0100".
    m = re.match(r'^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})\s+([+-]\d{2})(\d{2})$', text)
    if m:
        return "%sT%s%s:%s" % (m.group(1), m.group(2), m.group(3), m.group(4))
    m = re.match(r'^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})([+-]\d{2})(\d{2})$', text)
    if m:
        return "%sT%s%s:%s" % (m.group(1), m.group(2), m.group(3), m.group(4))
    return text


def _slug_tokens(value):
    value = (value or "").lower()
    value = html.unescape(value)
    tokens = re.findall(r'[a-z0-9]+', value)
    stop = {"and", "the", "a", "an", "of", "to", "for", "in", "at", "with", "remote", "web3", "crypto", "blockchain"}
    return [t for t in tokens if len(t) > 1 and t not in stop]


def _ld_jobpostings(page):
    for match in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page, re.I | re.S):
        raw = html.unescape(match.group(1).strip())
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            obj = stack.pop(0)
            if not isinstance(obj, dict):
                continue
            graph = obj.get("@graph")
            if isinstance(graph, list):
                stack.extend(graph)
            typ = obj.get("@type")
            if typ == "JobPosting" or (isinstance(typ, list) and "JobPosting" in typ):
                yield obj


def _job_links(listing_html):
    seen = set()
    host = urlparse(SITE).netloc
    for href in re.findall(r'href=["\']([^"\']+)["\']', listing_html):
        url = urljoin(SITE, html.unescape(href))
        parsed = urlparse(url)
        if parsed.netloc != host:
            continue
        if not re.search(r'/[^/]+/\d+$', parsed.path):
            continue
        canonical = parsed._replace(query="", fragment="").geturl()
        if canonical not in seen:
            seen.add(canonical)
            yield canonical


def _company(job):
    org = job.get("hiringOrganization")
    if isinstance(org, dict):
        return _clean_text(org.get("name"))
    return _clean_text(org)


def _match_url(job, candidates, used):
    title = _clean_text(job.get("title")) or ""
    company = _company(job) or ""
    wanted = set(_slug_tokens(title)[:5] + _slug_tokens(company)[:3])
    if not wanted:
        return None
    best_url = None
    best_score = 0
    for url in candidates:
        if url in used:
            continue
        path = urlparse(url).path.rsplit("/", 1)[0]
        tokens = set(_slug_tokens(path))
        score = len(wanted & tokens)
        if score > best_score:
            best_score = score
            best_url = url
    if best_score >= 2:
        used.add(best_url)
        return best_url
    for url in candidates:
        if url not in used:
            used.add(url)
            return url
    return None


def _location_from_place(place):
    if not isinstance(place, dict):
        return _clean_text(place)
    address = place.get("address")
    if isinstance(address, dict):
        parts = []
        for key in ("addressLocality", "addressRegion", "addressCountry"):
            val = _clean_text(address.get(key))
            if val and val not in parts:
                parts.append(val)
        if parts:
            return ", ".join(parts)
    return _clean_text(place.get("name"))


def _locations(job):
    locs = []
    raw = job.get("jobLocation")
    places = raw if isinstance(raw, list) else [raw]
    for place in places:
        loc = _location_from_place(place)
        if loc and loc not in locs:
            locs.append(loc)
    req = job.get("applicantLocationRequirements")
    reqs = req if isinstance(req, list) else [req]
    for item in reqs:
        name = _clean_text(item.get("name") if isinstance(item, dict) else item)
        if name and name not in locs:
            locs.append(name)
    if not locs and job.get("jobLocationType"):
        locs.append(_clean_text(job.get("jobLocationType")))
    return locs


def _salary_fields(job):
    out = {}
    base = job.get("baseSalary")
    if not isinstance(base, dict):
        return out
    currency = _clean_text(base.get("currency"))
    value = base.get("value")
    if isinstance(value, dict):
        min_value = value.get("minValue")
        max_value = value.get("maxValue")
        if min_value is None and max_value is None and value.get("value") is not None:
            min_value = max_value = value.get("value")
        if min_value is not None:
            out["salary_min"] = min_value
        if max_value is not None:
            out["salary_max"] = max_value
        unit = _clean_text(value.get("unitText"))
        if unit:
            out["salary_period"] = unit
    elif isinstance(value, (int, float)):
        out["salary_min"] = value
        out["salary_max"] = value
    if currency:
        out["salary_currency"] = currency
    return out


def crawl(ctx):
    used_urls = set()
    for page_num in (1, 2):
        listing_url = SITE.rstrip("/") + "/?page=%d" % page_num
        listing = _fetch(ctx, listing_url)
        candidates = list(_job_links(listing))
        for job in _ld_jobpostings(listing):
            url = _match_url(job, candidates, used_urls)
            if not url:
                continue

            title = _clean_text(job.get("title"))
            company = _company(job)
            description = _clean_text(job.get("description"))
            locs = _locations(job)
            location = locs[0] if locs else None
            if not (title and company and description and location):
                continue

            item = {
                "url": url,
                "title": title,
                "company": company,
                "location": location,
                "description": description,
                "source": SITE,
            }
            if len(locs) > 1:
                item["locations"] = locs

            employment_type = _clean_text(job.get("employmentType"))
            if employment_type:
                item["employment_type"] = employment_type
            workplace_type = _clean_text(job.get("jobLocationType"))
            if workplace_type:
                item["workplace_type"] = workplace_type
            posted_at = _normalize_date(job.get("datePosted"))
            if posted_at:
                item["posted_at"] = posted_at
            expires_at = _normalize_date(job.get("validThrough"))
            if expires_at:
                item["expires_at"] = expires_at
            department = _clean_text(job.get("industry"))
            if department:
                item["department"] = department

            item.update(_salary_fields(job))

            match = re.search(r'/(\d+)$', urlparse(url).path)
            if match:
                item["external_id"] = match.group(1)

            yield item
