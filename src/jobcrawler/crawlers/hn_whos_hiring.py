
import json
import re
import html

SITE = "https://hn.algolia.com/api/v1/search_by_date?query=Ask%20HN%3A%20Who%20is%20hiring%3F&tags=story"
EXTRA_HOSTS = []

_MONTHLY_RE = re.compile(r"^Ask HN:\s*Who is hiring\?\s*\([^)]+\)\s*$", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_URL_RE = re.compile(r"https?://[^\s<>()\"']+")
_HREF_RE = re.compile(r'href="([^"]+)"', re.I)
# A "how to apply" URL — an ATS/careers/form link is a direct application channel.
_APPLY_RE = re.compile(
    r"(?i)(career|/jobs?\b|jobs?\.|apply|lever|greenhouse|ashby|workable|gupy|"
    r"smartrecruiters|breezy|recruitee|wellfound|forms\.gle|docs\.google\.com/forms|typeform)"
)
# HN posters overwhelmingly say "email us at ...", often lightly obfuscated to dodge scrapers.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_OBFUS_AT = re.compile(r"\s*[\[({<]\s*at\s*[\])}>]\s*", re.I)
_OBFUS_DOT = re.compile(r"\s*[\[({<]\s*dot\s*[\])}>]\s*", re.I)
# A printed comp figure is k-suffixed ($150k), part of an explicit range
# ($150,000 - $200,000, $80-100k), or a lone comma-formatted amount ($150,000).
# Funding/GMV magnitudes ($3B, $875M/month, $1.6 trillion) must never match:
# an M/B/bn/billion/trillion suffix vetoes the amount, and a bare number only
# counts when it opens a range that ends in a real amount.
_AMOUNT_VETO = r"(?!\d|[.,]\d|\s?(?:[mbt]n?\b|[mb]illion\b|trillion\b))"
_AMOUNT = (
    r"(?:\d[\d,]*(?:\.\d+)?\s?k(?![a-z])|\d{1,3}(?:,\d{3})+(?:\.\d+)?" + _AMOUNT_VETO + r")"
)
_SALARY_RE = re.compile(
    r"(?i)(?:[$€£]\s?(?:" + _AMOUNT + r"(?:\s?[-–—]\s?[$€£]?\s?" + _AMOUNT + r")?"
    r"|\d+(?:\.\d+)?\s?[-–—]\s?[$€£]?\s?" + _AMOUNT + r")"
    r"(?:\s?(?:/|per)\s?(?:year|yr|hour|hr))?"
    r"|(?:salary|compensation|pay)\s*[:\-]\s*[^.|;\n]{3,80})"
)

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
    # A header location fragment is short and never a URL. Slash-style posts with a
    # '|' deep in the body make parts[0] the whole 400-char head, which would match
    # on a stray "remote"/"onsite" and stuff pitch text + URLs into `location`.
    if len(part) > 80 or _URL_RE.search(part):
        return False
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

def _find_email(*texts):
    # Plain address first; then de-obfuscate the common HN "user [at] host [dot] com" forms.
    for raw in texts:
        if not raw:
            continue
        m = _EMAIL_RE.search(raw)
        if m:
            return m.group(0).rstrip(".,;:)")
        deob = _OBFUS_DOT.sub(".", _OBFUS_AT.sub("@", raw))
        m = _EMAIL_RE.search(deob)
        if m:
            return m.group(0).rstrip(".,;:)")
    return None

def _apply_link(raw_html, text):
    # Applications on HN are usually an email; when there's no direct ATS/careers link, the
    # apply link should be a mailto: to that address rather than the HN item URL.
    # Algolia's comment_text entity-escapes attribute values ("&#x2F;" for "/", "&amp;"),
    # so each captured href must be unescaped or the stored apply_url is garbage.
    hrefs = [html.unescape(h) for h in _HREF_RE.findall(raw_html or "")]
    urls = [h.rstrip(".,;)") for h in hrefs if h.lower().startswith("http")]
    urls += [u.rstrip(".,;)") for u in _URL_RE.findall(text or "") if u.rstrip(".,;)") not in urls]

    ats = [u for u in urls if _APPLY_RE.search(u)]
    if ats:
        return ats[0]  # a real application form beats an email

    mailto = next((h for h in hrefs if h.lower().startswith("mailto:")), None)
    email = mailto[len("mailto:"):].split("?")[0] if mailto else _find_email(text, raw_html)
    if email:
        return "mailto:" + email

    return urls[0] if urls else None

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
        raw = comment.get("text")
        text = _clean(raw)
        cid = str(comment.get("id") or "")
        if not text or not cid or cid in seen:
            continue
        seen.add(cid)

        parts = _split_header(text)
        company, title, location, employment_type = _parse_header(parts, comment.get("author"))

        job = {
            # The human-facing HN comment permalink, not the JSON API endpoint — this is the
            # posting's canonical link (and its recheckable/dedup identity).
            "url": "https://news.ycombinator.com/item?id=" + cid,
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

        # How to apply — an ATS/careers URL if given, otherwise a mailto: to the posted
        # email (the usual HN channel), so the tracker's Apply link opens a draft email.
        apply = _apply_link(raw, text)
        if apply:
            job["apply_url"] = apply

        yield _make_job(**job)
