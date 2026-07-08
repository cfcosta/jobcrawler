
import json
import re
import html

SITE = "https://hn.algolia.com/api/v1/search_by_date?query=Ask%20HN%3A%20Who%20is%20hiring%3F&tags=story"
EXTRA_HOSTS = []

_MONTHLY_RE = re.compile(r"^Ask HN:\s*Who is hiring\?\s*\([^)]+\)\s*$", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_URL_RE = re.compile(r"https?://[^\s<>()\"']+")
_SALARY_RE = re.compile(r"(?i)(?:[$€£]\s?\d[\d,.]*(?:\s?[kKmM])?(?:\s?[-–]\s?[$€£]?\s?\d[\d,.]*(?:\s?[kKmM])?)?(?:\s?(?:/|per)\s?(?:year|yr|hour|hr))?|(?:salary|compensation|pay)\s*[:\-]\s*[^.|;\n]{3,80})")

def _clean(value):
    if value is None:
        return None
    value = html.unescape(str(value))
    value = re.sub(r"<p\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = _TAG_RE.sub(" ", value)
    value = _WS_RE.sub(" ", value).strip()
    return value or None

def _split_header(text):
    head = (text or "")[:400]
    if "|" in head:
        return [p.strip(" -–—\t\r\n") for p in head.split("|") if p.strip(" -–—\t\r\n")]
    return []

def _looks_location(part):
    p = part.lower()
    tokens = [
        "remote", "hybrid", "onsite", "on-site", "nyc", "new york", "sf", "san francisco",
        "london", "berlin", "paris", "toronto", "canada", "us only", "usa", "united states",
        "europe", "eu", "germany", "uk", "india", "australia", "worldwide", "global",
        "wa", "ca", "ma", "tx", "co", "seattle", "boston", "chicago", "austin"
    ]
    return any(t in p for t in tokens) or bool(re.search(r"\b[A-Z][a-z]+,\s*[A-Z]{2}\b", part))

def _employment(part):
    p = part.lower()
    if "full-time" in p or "full time" in p:
        return "Full-time"
    if "part-time" in p or "part time" in p:
        return "Part-time"
    if "contract" in p or "freelance" in p:
        return "Contract"
    if "intern" in p:
        return "Internship"
    return None

def _workplace(parts, text):
    joined = " | ".join(parts).lower() + " " + (text or "").lower()[:500]
    if "remote" in joined:
        return "Remote"
    if "hybrid" in joined:
        return "Hybrid"
    if "onsite" in joined or "on-site" in joined or "in-office" in joined:
        return "On-site"
    return None

def _salary(text):
    m = _SALARY_RE.search(text or "")
    return m.group(0).strip() if m else None

def _is_salary_part(part):
    return bool(_SALARY_RE.search(part or ""))

def _apply_url(text):
    urls = _URL_RE.findall(text or "")
    if not urls:
        return None
    preferred = [u for u in urls if re.search(r"(?i)(career|job|apply|lever|greenhouse|ashby|workable|notion)", u)]
    return (preferred or urls)[0].rstrip(".,;")

def _make_job(**kwargs):
    try:
        return Job(**kwargs)
    except NameError:
        return kwargs

def _parse_header(parts, author):
    company = None
    title = None
    location = None
    employment_type = None

    remaining = list(parts)
    if remaining and _looks_location(remaining[0]) and len(remaining) > 1:
        location = remaining.pop(0)

    if remaining:
        company = remaining.pop(0)

    for part in remaining:
        emp = _employment(part)
        if emp and not employment_type:
            employment_type = emp
            continue
        if _looks_location(part):
            location = (location + " / " + part) if location else part
            continue
        if _is_salary_part(part):
            continue
        if title is None:
            title = part

    if not company:
        company = author or "Unknown"
    if not title:
        title = company + " roles"
    if not location:
        location = "Not specified"

    return company, title, location, employment_type

def crawl(ctx):
    search = json.loads(ctx.fetch(SITE))
    thread = None
    for hit in search.get("hits", []):
        title = hit.get("title") or hit.get("story_title") or ""
        if _MONTHLY_RE.match(title):
            thread = hit
            break
    if not thread:
        return

    thread_id = str(thread.get("objectID") or thread.get("id"))
    item = json.loads(ctx.fetch("https://hn.algolia.com/api/v1/items/" + thread_id))
    thread_title = item.get("title") or thread.get("title") or "Ask HN: Who is hiring?"

    seen = set()
    for comment in item.get("children") or []:
        text = _clean(comment.get("text"))
        cid = str(comment.get("id") or "")
        if not text or not cid or cid in seen:
            continue
        seen.add(cid)

        parts = _split_header(text)
        company, title, location, employment_type = _parse_header(parts, comment.get("author"))

        job = {
            "url": "https://hn.algolia.com/api/v1/items/" + cid,
            "title": title,
            "company": company,
            "location": location,
            "description": text,
            "source": thread_title,
            "external_id": cid,
        }

        posted_at = comment.get("created_at")
        if posted_at:
            job["posted_at"] = posted_at

        workplace_type = _workplace(parts, text)
        if workplace_type:
            job["workplace_type"] = workplace_type

        if employment_type:
            job["employment_type"] = employment_type

        sal = _salary(text)
        if sal:
            job["salary"] = sal

        apply = _apply_url(text)
        if apply:
            job["apply_url"] = apply

        yield _make_job(**job)
