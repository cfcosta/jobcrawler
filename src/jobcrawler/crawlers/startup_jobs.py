
import json, re

SITE = "https://startup.jobs/roles/software-engineer/remote?page=2"
EXTRA_HOSTS = []

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

def fetch_text(ctx, url):
    try:
        return ctx.fetch(url, headers=HEADERS)
    except TypeError:
        return ctx.fetch(url)

def abs_url(url):
    if not url:
        return None
    if url.startswith("https://startup.jobs/"):
        return url
    if url.startswith("/"):
        return "https://startup.jobs" + url
    return url

def clean_text(value):
    value = value or ""
    value = re.sub(r"(?is)<(script|style).*?</\1>", " ", value)
    value = re.sub(r"(?is)<br\s*/?>", "\n", value)
    value = re.sub(r"(?is)</p\s*>|</li\s*>|</div\s*>|</h[1-6]\s*>", "\n", value)
    value = re.sub(r"(?is)<[^>]+>", " ", value)
    entities = {
        "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"',
        "&#39;": "'", "&#x27;": "'", "&nbsp;": " "
    }
    for old, new in entities.items():
        value = value.replace(old, new)
    value = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), value)
    value = re.sub(r"&#x([0-9a-fA-F]+);", lambda m: chr(int(m.group(1), 16)), value)
    return re.sub(r"\s+", " ", value).strip()

def as_jobposting(obj):
    if isinstance(obj, dict):
        typ = obj.get("@type")
        if typ == "JobPosting" or (isinstance(typ, list) and "JobPosting" in typ):
            return obj
        graph = obj.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                found = as_jobposting(item)
                if found:
                    return found
    if isinstance(obj, list):
        for item in obj:
            found = as_jobposting(item)
            if found:
                return found
    return None

def json_ld_job(page):
    for match in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page, re.I | re.S):
        raw = match.group(1).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        found = as_jobposting(data)
        if found:
            return found
    return None

def listing_links(page):
    seen = set()
    links = []
    for href in re.findall(r'<a\b[^>]*\bhref=["\']([^"\']+)["\'][^>]*>', page, re.I):
        url = abs_url(href)
        if not url:
            continue
        if not url.startswith("https://startup.jobs/"):
            continue
        if "/roles/" in url or "/company/" in url or "/locations/" in url or "/apply" in url:
            continue
        path = url.split("?", 1)[0].rstrip("/")
        if re.search(r"-\d+$", path) and path not in seen:
            seen.add(path)
            links.append(path)
    return links

def next_page(page):
    patterns = [
        r'<a\b[^>]*rel=["\']next["\'][^>]*href=["\']([^"\']+)["\']',
        r'<a\b[^>]*data-infinite-scroll-target=["\']nextPageLink["\'][^>]*href=["\']([^"\']+)["\']',
        r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*(?:rel=["\']next["\']|data-infinite-scroll-target=["\']nextPageLink["\'])',
    ]
    for pat in patterns:
        m = re.search(pat, page, re.I | re.S)
        if m:
            return abs_url(m.group(1))
    return None

def location_value(data):
    loc = data.get("jobLocation")
    if isinstance(loc, list):
        loc = loc[0] if loc else None
    if isinstance(loc, dict):
        addr = loc.get("address")
        if isinstance(addr, dict):
            parts = []
            for key in ("addressLocality", "addressRegion", "addressCountry"):
                val = addr.get(key)
                if val:
                    parts.append(str(val))
            if parts:
                return ", ".join(parts)
        name = loc.get("name")
        if name:
            return str(name)
    req = data.get("applicantLocationRequirements")
    if isinstance(req, list):
        names = [str(x.get("name")) for x in req if isinstance(x, dict) and x.get("name")]
        if names:
            return ", ".join(names)
    if isinstance(req, dict) and req.get("name"):
        return str(req.get("name"))
    if data.get("jobLocationType"):
        return str(data.get("jobLocationType"))
    return "Remote"

def all_locations(data, primary):
    vals = []
    loc = data.get("jobLocation")
    items = loc if isinstance(loc, list) else [loc]
    for item in items:
        if isinstance(item, dict):
            addr = item.get("address")
            if isinstance(addr, dict):
                parts = []
                for key in ("addressLocality", "addressRegion", "addressCountry"):
                    val = addr.get(key)
                    if val:
                        parts.append(str(val))
                if parts:
                    vals.append(", ".join(parts))
                    continue
            if item.get("name"):
                vals.append(str(item.get("name")))
    if not vals and primary:
        vals.append(primary)
    out = []
    for val in vals:
        if val and val not in out:
            out.append(val)
    return out

def add_if_present(job, key, value):
    if value is not None and value != "":
        job[key] = value

def build_job(url, data):
    org = data.get("hiringOrganization") or {}
    primary_location = location_value(data)
    description = clean_text(data.get("description") or "")
    job = {
        "url": url,
        "title": data.get("title"),
        "company": org.get("name") or "Startup.jobs",
        "location": primary_location,
        "description": description,
        "source": SITE,
    }

    locs = all_locations(data, primary_location)
    if len(locs) > 1:
        job["locations"] = locs

    add_if_present(job, "employment_type", data.get("employmentType"))
    if data.get("jobLocationType") == "TELECOMMUTE":
        job["workplace_type"] = "remote"
    elif data.get("jobLocationType"):
        job["workplace_type"] = data.get("jobLocationType")
    add_if_present(job, "posted_at", data.get("datePosted"))
    add_if_present(job, "expires_at", data.get("validThrough"))
    add_if_present(job, "external_id", str(data.get("identifier")) if data.get("identifier") else url.rsplit("-", 1)[-1])

    apply_url = data.get("directApply")
    if isinstance(apply_url, str):
        add_if_present(job, "apply_url", apply_url)

    salary = data.get("baseSalary")
    if isinstance(salary, dict):
        add_if_present(job, "salary_currency", salary.get("currency"))
        value = salary.get("value")
        if isinstance(value, dict):
            add_if_present(job, "salary_min", value.get("minValue"))
            add_if_present(job, "salary_max", value.get("maxValue"))
            add_if_present(job, "salary_period", value.get("unitText"))
        elif value is not None:
            add_if_present(job, "salary_min", value)
            add_if_present(job, "salary_max", value)

    return job

def crawl(ctx):
    seen_pages = set()
    seen_jobs = set()
    page_url = SITE
    pages = 0

    while page_url and page_url not in seen_pages and pages < 1:
        seen_pages.add(page_url)
        pages += 1
        page = fetch_text(ctx, page_url)

        for job_url in listing_links(page):
            if job_url in seen_jobs:
                continue
            seen_jobs.add(job_url)
            detail = fetch_text(ctx, job_url)
            data = json_ld_job(detail)
            if not data:
                continue
            job = build_job(job_url, data)
            if job.get("title") and job.get("description"):
                yield job

        page_url = next_page(page)
