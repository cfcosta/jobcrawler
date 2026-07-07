import html
import json
import re
from urllib.parse import urljoin

SITE = "https://jobs.solana.com/"
EXTRA_HOSTS = []

SOURCE = "solana"
MAX_JOBS = 6

_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', re.I | re.S
)
_URL_BLOCK_RE = re.compile(r"<url\b[^>]*>(.*?)</url>", re.I | re.S)
_LOC_RE = re.compile(r"<loc\b[^>]*>(.*?)</loc>", re.I | re.S)
_LASTMOD_RE = re.compile(r"<lastmod\b[^>]*>(.*?)</lastmod>", re.I | re.S)
_JOB_PATH_RE = re.compile(r"/companies/[^/]+/jobs/[^/?#]+")

_SCRIPT_STYLE_RE = re.compile(r"(?is)<\s*(script|style)\b.*?</\s*\1\s*>")
_BR_RE = re.compile(r"(?i)<\s*br\s*/?\s*>")
_BLOCK_END_RE = re.compile(r"(?i)</\s*(p|div|li|tr|h1|h2|h3|h4|ul|ol)\s*>")
_LI_RE = re.compile(r"(?i)<\s*li\b[^>]*>")
_TAG_RE = re.compile(r"(?s)<[^>]+>")
_INLINE_WS_RE = re.compile(r"[ \t\r\f\v]+")
_PAD_NL_RE = re.compile(r" *\n *")
_MULTI_NL_RE = re.compile(r"\n{3,}")


def _clean(value):
    if isinstance(value, str):
        value = html.unescape(value).strip()
        return value or None
    return None


def _strip_html(value):
    if not value:
        return None
    text = str(value)
    text = _SCRIPT_STYLE_RE.sub(" ", text)
    text = _BR_RE.sub("\n", text)
    text = _BLOCK_END_RE.sub("\n", text)
    text = _LI_RE.sub("\n- ", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _INLINE_WS_RE.sub(" ", text)
    text = _PAD_NL_RE.sub("\n", text)
    text = _MULTI_NL_RE.sub("\n\n", text)
    return text.strip() or None


def _first_text(value):
    if isinstance(value, dict):
        for key in ("name", "label", "title", "value"):
            found = _clean(value.get(key))
            if found:
                return found
        return None
    return _clean(value)


def _join_names(items):
    names = []
    if isinstance(items, list):
        for item in items:
            name = _first_text(item)
            if name:
                names.append(name)
    elif items:
        name = _first_text(items)
        if name:
            names.append(name)
    return ", ".join(dict.fromkeys(names)) or None


def _next_data(page):
    match = _NEXT_DATA_RE.search(page)
    if not match:
        return None
    raw = match.group(1)
    for candidate in (html.unescape(raw), raw):
        try:
            return json.loads(candidate)
        except ValueError:
            continue
    return None


def _sitemap_job_urls(sitemap):
    rows = []
    for idx, block in enumerate(_URL_BLOCK_RE.findall(sitemap)):
        loc_match = _LOC_RE.search(block)
        if not loc_match:
            continue
        loc = html.unescape(loc_match.group(1)).strip()
        if not _JOB_PATH_RE.search(loc):
            continue
        lm_match = _LASTMOD_RE.search(block)
        lastmod = lm_match.group(1).strip() if lm_match else ""
        rows.append((lastmod, -idx, loc))
    rows.sort(reverse=True)
    seen = set()
    ordered = []
    for _, __, loc in rows:
        if loc not in seen:
            seen.add(loc)
            ordered.append(loc)
    return ordered


def _money_from_cents(value):
    try:
        amount = int(value) / 100.0
    except (TypeError, ValueError):
        return None
    if amount.is_integer():
        return str(int(amount))
    return f"{amount:.2f}".rstrip("0").rstrip(".")


def _salary(job):
    if not job.get("compensationPublic"):
        return None
    low = _money_from_cents(job.get("compensationAmountMinCents"))
    high = _money_from_cents(job.get("compensationAmountMaxCents"))
    if not low and not high:
        return None
    currency = _clean(job.get("compensationCurrency"))
    period = _clean(job.get("compensationPeriod"))
    if period == "period_not_defined":
        period = None
    if low and high and low != high:
        text = f"{low} - {high}"
    else:
        text = low or high
    if currency:
        text = f"{currency} {text}"
    if period:
        text = f"{text} / {period}"
    return text


def _apply_url(job):
    path = _clean(job.get("applicationPath"))
    if path:
        return urljoin(SITE, path)
    return _clean(job.get("url"))


def _build(job, page_url):
    if not isinstance(job, dict):
        return None
    status = _clean(job.get("status"))
    if status and status.lower() != "active":
        return None
    if job.get("deactivatedAt") or job.get("closedAt"):
        return None

    title = _clean(job.get("title"))
    description = _strip_html(job.get("description"))
    organization = job.get("organization")
    company = (
        _clean(organization.get("name")) if isinstance(organization, dict) else None
    )
    location = _join_names(job.get("locations"))
    if not (title and description and company and location):
        return None

    out = {
        "url": page_url,
        "title": title,
        "company": company,
        "location": location,
        "description": description,
        "source": SOURCE,
    }

    ident = job.get("id")
    if ident is not None:
        ident = str(ident).strip()
        if ident:
            out["external_id"] = ident

    posted_at = _clean(job.get("postedAt"))
    if posted_at:
        out["posted_at"] = posted_at

    expires_at = _clean(job.get("expiresAt"))
    if expires_at:
        out["expires_at"] = expires_at

    employment_type = _join_names(job.get("employmentTypes"))
    if employment_type:
        out["employment_type"] = employment_type

    department = _join_names(job.get("jobFunctions"))
    if department:
        out["department"] = department

    salary = _salary(job)
    if salary:
        out["salary"] = salary

    apply_url = _apply_url(job)
    if apply_url:
        out["apply_url"] = apply_url

    return out


def crawl(ctx):
    sitemap = ctx.fetch(urljoin(SITE, "sitemap.xml"))
    yielded = 0
    for url in _sitemap_job_urls(sitemap):
        if yielded >= MAX_JOBS:
            break
        try:
            page = ctx.fetch(url)
        except Exception:
            continue
        data = _next_data(page)
        if not isinstance(data, dict):
            continue
        props = data.get("props")
        page_props = props.get("pageProps") if isinstance(props, dict) else None
        state = page_props.get("initialState") if isinstance(page_props, dict) else None
        jobs = state.get("jobs") if isinstance(state, dict) else None
        current = jobs.get("currentJob") if isinstance(jobs, dict) else None
        item = _build(current, url)
        if item:
            yielded += 1
            yield item
