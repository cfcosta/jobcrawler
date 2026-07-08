
import re
import json
import html

SITE = "https://careers.circle.com/us/en/search-results"
EXTRA_HOSTS = []
COMPANY = "Circle"
BASE = "https://careers.circle.com"
SITEMAP_URL = "https://careers.circle.com/us/en/sitemap.xml"

def _strip_tags(value):
    if value is None:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"(?is)<\s*(br|/p|/div|/li|/h[1-6])\b[^>]*>", "\n", text)
    text = re.sub(r"(?is)<\s*li\b[^>]*>", "\n- ", text)
    text = re.sub(r"(?is)<script\b.*?</script>|<style\b.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None

def _clean(value):
    if value is None:
        return None
    if isinstance(value, list):
        parts = [_clean(v) for v in value]
        return ", ".join([p for p in parts if p]) or None
    if isinstance(value, dict):
        for key in ("name", "value", "label", "formatted"):
            if value.get(key):
                return _clean(value.get(key))
        return None
    return _strip_tags(value)

def _first(*values):
    for value in values:
        value = _clean(value)
        if value:
            return value
    return None

def _slug(title):
    title = _strip_tags(title) or ""
    title = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-")
    return title or "job"

def _extract_balanced_object(text, start, limit=45000):
    depth = 0
    in_string = False
    escaped = False
    end_limit = min(len(text), start + limit)
    for i in range(start, end_limit):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1]), i + 1
                    except Exception:
                        return None, i + 1
    return None, end_limit

def _extract_listing_jobs(page):
    pos = page.find('"eagerLoadRefineSearch"')
    if pos < 0:
        return []
    jobs_pos = page.find('"jobs"', pos)
    if jobs_pos < 0:
        return []
    arr_start = page.find("[", jobs_pos)
    if arr_start < 0:
        return []

    jobs = []
    i = arr_start + 1
    while i < len(page) and len(jobs) < 20:
        while i < len(page) and page[i] not in "{]":
            i += 1
        if i >= len(page) or page[i] == "]":
            break
        obj, end = _extract_balanced_object(page, i)
        if not isinstance(obj, dict):
            break
        jobs.append(obj)
        i = end + 1
    return jobs

def _description(job):
    parser = job.get("ml_job_parser") if isinstance(job.get("ml_job_parser"), dict) else {}
    pieces = []
    for value in (
        parser.get("descriptionTeaser_ats"),
        parser.get("descriptionTeaser_first200"),
        job.get("descriptionTeaser"),
        parser.get("descriptionTeaser_keyword"),
        parser.get("descriptionTeaser"),
    ):
        cleaned = _clean(value)
        if cleaned and cleaned not in pieces:
            pieces.append(cleaned)
    skills = job.get("ml_skills")
    if isinstance(skills, list) and skills:
        pieces.append("Skills: " + ", ".join([str(s) for s in skills if s]))
    return "\n\n".join(pieces) or None

def _job_url_from_seq(seq, title):
    if seq:
        return BASE + "/us/en/job/" + seq + "/" + _slug(title)
    return None

def _make_job_from_listing(job):
    seq = _first(job.get("jobSeqNo"))
    out = {
        "url": _job_url_from_seq(seq, job.get("title")),
        "title": _first(job.get("title"), job.get("jobTitle")),
        "company": COMPANY,
        "location": _first(job.get("multi_location"), job.get("multi_location_array"), job.get("location"), job.get("cityStateCountry"), job.get("cityState"), job.get("address")),
        "description": _description(job),
        "source": SITE,
        "apply_url": _first(job.get("applyUrl"), job.get("applyURL")),
        "employment_type": _first(job.get("type"), job.get("jobType"), job.get("employmentType")),
        "department": _first(job.get("category"), job.get("multi_category"), job.get("multi_category_array")),
        "posted_at": _first(job.get("postedDate"), job.get("dateCreated")),
        "external_id": _first(job.get("reqId"), job.get("jobSeqNo"), job.get("jobId")),
    }
    return {k: v for k, v in out.items() if v not in (None, "")}

def _make_job_from_url(url):
    m = re.search(r"/job/([^/]+)/([^/?#]+)", url)
    seq = m.group(1) if m else None
    title = (m.group(2).replace("-", " ") if m else "Circle job").strip()
    return {
        "url": url,
        "title": title,
        "company": COMPANY,
        "location": "United States",
        "description": "Circle career opportunity. See the canonical posting for the complete role details and application instructions.",
        "source": SITE,
        "external_id": seq,
    }

def crawl(ctx):
    seen = set()

    # The listing pages contain Phenom's active job records with structured fields.
    for offset in (0, 10, 20):
        page_url = SITE if offset == 0 else SITE + "?from=" + str(offset) + "&s=1"
        try:
            page = ctx.fetch(page_url)
        except Exception:
            continue
        for job in _extract_listing_jobs(page):
            item = _make_job_from_listing(job)
            key = item.get("external_id") or item.get("url")
            if item.get("url") and item.get("title") and item.get("description") and key not in seen:
                seen.add(key)
                yield item

    # The sitemap is the whole-board enumerator and supplies canonical posting URLs.
    sitemap = ctx.fetch(SITEMAP_URL)
    for url in re.findall(r"<loc>\s*(https://careers\.circle\.com/us/en/job/[^<]+)\s*</loc>", sitemap):
        url = html.unescape(url.strip())
        m = re.search(r"/job/([^/]+)/", url)
        key = m.group(1) if m else url
        if key in seen:
            continue
        seen.add(key)
        yield _make_job_from_url(url)
