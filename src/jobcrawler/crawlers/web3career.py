
import html
import json
import re
from urllib.parse import urljoin

SITE = "https://web3.career/"
EXTRA_HOSTS = []


def _text(value):
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = html.unescape(value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def _date(value):
    value = _text(value)
    if not value:
        return None
    m = re.match(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})\s+([+-]\d{2})(\d{2})$", value)
    if m:
        return "%sT%s%s:%s" % (m.group(1), m.group(2), m.group(3), m.group(4))
    return value


def _slug_tokens(value):
    value = _text(value) or ""
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return [p for p in value.split("-") if len(p) > 2]


def _iter_json_objects(script_text):
    text = html.unescape(script_text or "").strip()
    if not text:
        return
    try:
        yield json.loads(text)
        return
    except Exception:
        pass

    decoder = json.JSONDecoder()
    pos = 0
    while pos < len(text):
        start = text.find("{", pos)
        if start < 0:
            break
        try:
            obj, end = decoder.raw_decode(text[start:])
        except Exception:
            pos = start + 1
            continue
        yield obj
        pos = start + end


def _walk_jobpostings(obj):
    if isinstance(obj, dict):
        typ = obj.get("@type")
        if typ == "JobPosting" or (isinstance(typ, list) and "JobPosting" in typ):
            yield obj
        for value in obj.values():
            yield from _walk_jobpostings(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk_jobpostings(value)


def _jobpostings_from_page(page):
    scripts = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        page or "",
        re.I | re.S,
    )
    for script in scripts:
        for obj in _iter_json_objects(script):
            yield from _walk_jobpostings(obj)


def _listing_links(page):
    links = []
    seen = set()
    for href in re.findall(r'href=["\']([^"\']+/\d+)(?:[?#][^"\']*)?["\']', page or "", re.I):
        url = urljoin(SITE, html.unescape(href))
        if not url.startswith(SITE) or url in seen:
            continue
        seen.add(url)
        links.append(url)
    return links


def _company(job):
    org = job.get("hiringOrganization")
    if isinstance(org, dict):
        return _text(org.get("name"))
    return _text(org)


def _location_from_schema(job):
    loc = job.get("jobLocation")
    if isinstance(loc, list):
        loc = loc[0] if loc else None

    parts = []
    if isinstance(loc, dict):
        addr = loc.get("address")
        if isinstance(addr, dict):
            for key in ("addressLocality", "addressRegion", "addressCountry"):
                val = _text(addr.get(key))
                if val and val not in parts:
                    parts.append(val)
        elif isinstance(addr, str):
            val = _text(addr)
            if val:
                parts.append(val)

        for key in ("name", "addressLocality"):
            val = _text(loc.get(key))
            if val and val not in parts:
                parts.append(val)

    if parts:
        return ", ".join(parts)
    if _text(job.get("jobLocationType")) == "TELECOMMUTE":
        return "Remote"
    return None


def _locations_from_schema(job):
    locs = []
    raw = job.get("jobLocation")
    items = raw if isinstance(raw, list) else [raw]
    for loc in items:
        pseudo = {"jobLocation": loc, "jobLocationType": job.get("jobLocationType")}
        val = _location_from_schema(pseudo)
        if val and val not in locs:
            locs.append(val)
    return locs


def _salary_fields(job):
    out = {}
    salary = job.get("baseSalary")
    if not isinstance(salary, dict):
        return out

    currency = _text(salary.get("currency"))
    value = salary.get("value")
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        min_value = value.get("minValue")
        max_value = value.get("maxValue")
        single_value = value.get("value")
        if min_value is None and single_value is not None:
            min_value = single_value
        if max_value is None and single_value is not None:
            max_value = single_value
        if min_value is not None:
            out["salary_min"] = min_value
        if max_value is not None:
            out["salary_max"] = max_value
        period = _text(value.get("unitText"))
        if period:
            out["salary_period"] = period
    elif value is not None:
        out["salary_min"] = value
        out["salary_max"] = value

    if currency:
        out["salary_currency"] = currency
    return out


def _best_url_for_job(job, links, used, index, total_jobs):
    title_tokens = _slug_tokens(job.get("title"))
    company_tokens = _slug_tokens(_company(job))
    wanted = title_tokens[:4] + company_tokens[:3]

    best_url = None
    best_score = -1
    for url in links:
        if url in used:
            continue
        slug = url.rstrip("/").rsplit("/", 2)[-2].lower()
        score = sum(1 for token in wanted if token in slug)
        if score > best_score:
            best_score = score
            best_url = url

    if best_url and best_score >= 2:
        return best_url

    # Sponsored rows can appear before the JSON-LD sequence.
    offset = max(0, len(links) - total_jobs)
    pos = index + offset
    if pos < len(links) and links[pos] not in used:
        return links[pos]
    for url in links:
        if url not in used:
            return url
    return None


def _job_from_schema(data, url):
    title = _text(data.get("title"))
    description = _text(data.get("description"))
    company = _company(data)
    location = _location_from_schema(data)
    if not (url and title and description and company and location):
        return None

    job = {
        "url": url,
        "title": title,
        "company": company,
        "location": location,
        "description": description,
        "source": SITE,
    }

    external_id = url.rstrip("/").rsplit("/", 1)[-1]
    if external_id.isdigit():
        job["external_id"] = external_id

    locations = _locations_from_schema(data)
    if len(locations) > 1:
        job["locations"] = locations

    employment_type = _text(data.get("employmentType"))
    if employment_type:
        job["employment_type"] = employment_type

    workplace_type = _text(data.get("jobLocationType"))
    if workplace_type:
        job["workplace_type"] = workplace_type

    posted_at = _date(data.get("datePosted"))
    if posted_at:
        job["posted_at"] = posted_at

    expires_at = _date(data.get("validThrough"))
    if expires_at:
        job["expires_at"] = expires_at

    job.update(_salary_fields(data))
    return job


def crawl(ctx):
    yielded = set()

    for page_num in (1, 2):
        listing_url = SITE if page_num == 1 else SITE + "?page=2"
        page = ctx.fetch(listing_url)

        links = _listing_links(page)
        postings = list(_jobpostings_from_page(page))
        used_urls = set()

        for index, data in enumerate(postings):
            url = _best_url_for_job(data, links, used_urls, index, len(postings))
            if not url or url in yielded:
                continue
            used_urls.add(url)
            yielded.add(url)
            job = _job_from_schema(data, url)
            if job:
                yield job
