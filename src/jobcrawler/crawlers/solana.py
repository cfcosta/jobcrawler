import datetime
import html
import json
import re
from urllib.parse import urljoin

SITE = "https://jobs.solana.com/"
EXTRA_HOSTS = []

SOURCE = "solana"
MAX_JOBS = 8
DEADLINE_SECONDS = 6.5

_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', re.I | re.S
)
_URL_BLOCK_RE = re.compile(r"<url\b[^>]*>(.*?)</url>", re.I | re.S)
_LOC_RE = re.compile(r"<loc\b[^>]*>(.*?)</loc>", re.I | re.S)
_LASTMOD_RE = re.compile(r"<lastmod\b[^>]*>(.*?)</lastmod>", re.I | re.S)
_JOB_PATH_RE = re.compile(r"/companies/[^/]+/jobs/[^/?#]+")

_SCRIPT_STYLE_RE = re.compile(r"(?is)<\s*(script|style)\b.*?</\s*\1\s*>")
_BR_RE = re.compile(r"(?i)<\s*br\s*/?\s*>")
_BLOCK_END_RE = re.compile(
    r"(?i)</\s*(p|div|li|tr|h1|h2|h3|h4|h5|h6|ul|ol|section|header)\s*>"
)
_LI_RE = re.compile(r"(?i)<\s*li\b[^>]*>")
_TAG_RE = re.compile(r"(?s)<[^>]+>")
_INLINE_WS_RE = re.compile(r"[ \t\r\f\v]+")
_PAD_NL_RE = re.compile(r" *\n *")
_MULTI_NL_RE = re.compile(r"\n{3,}")
_HTTP_RE = re.compile(r"(?i)^https?://")

_PERIOD_SENTINELS = {"period_not_defined", "not_defined", ""}


def _clean(value):
    if isinstance(value, str):
        value = html.unescape(value).strip()
        return value or None
    return None


def _strip_html(value):
    if not isinstance(value, str) or not value:
        return None
    text = _SCRIPT_STYLE_RE.sub(" ", value)
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
    for candidate in (raw, html.unescape(raw)):
        try:
            return json.loads(candidate)
        except ValueError:
            continue
    return None


def _current_job(data):
    if not isinstance(data, dict):
        return None
    props = data.get("props")
    page_props = props.get("pageProps") if isinstance(props, dict) else None
    state = page_props.get("initialState") if isinstance(page_props, dict) else None
    jobs = state.get("jobs") if isinstance(state, dict) else None
    current = jobs.get("currentJob") if isinstance(jobs, dict) else None
    return current if isinstance(current, dict) else None


def _sitemap_job_urls(sitemap):
    rows = []
    seen = set()
    for block in _URL_BLOCK_RE.findall(sitemap):
        loc_match = _LOC_RE.search(block)
        if not loc_match:
            continue
        loc = html.unescape(loc_match.group(1)).strip()
        if loc in seen or not _JOB_PATH_RE.search(loc):
            continue
        seen.add(loc)
        lastmod_match = _LASTMOD_RE.search(block)
        lastmod = lastmod_match.group(1).strip() if lastmod_match else ""
        rows.append((lastmod, loc))
    rows.sort(reverse=True)
    return [loc for _, loc in rows]


def _cents(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) / 100.0


def _apply_salary(job, out):
    if not job.get("compensationPublic"):
        return
    low = _cents(job.get("compensationAmountMinCents"))
    high = _cents(job.get("compensationAmountMaxCents"))
    if low is None and high is None:
        return
    if low is not None:
        out["salary_min"] = low
    if high is not None:
        out["salary_max"] = high
    currency = _clean(job.get("compensationCurrency"))
    if currency:
        out["salary_currency"] = currency
    period = _clean(job.get("compensationPeriod"))
    if period and period.lower() not in _PERIOD_SENTINELS:
        out["salary_period"] = period


def _apply_url(job):
    external = _clean(job.get("url"))
    if external and _HTTP_RE.match(external):
        return external
    path = _clean(job.get("applicationPath"))
    if path:
        return urljoin(SITE, path)
    return None


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
    if isinstance(ident, (int, str)):
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

    apply_url = _apply_url(job)
    if apply_url:
        out["apply_url"] = apply_url

    _apply_salary(job, out)
    return out


def crawl(ctx):
    start = datetime.datetime.now()
    sitemap = ctx.fetch(urljoin(SITE, "/sitemap.xml"))
    yielded = 0
    for page_url in _sitemap_job_urls(sitemap):
        if yielded >= MAX_JOBS:
            break
        if (datetime.datetime.now() - start).total_seconds() > DEADLINE_SECONDS:
            break
        try:
            page = ctx.fetch(page_url)
        except Exception:
            continue
        job = _build(_current_job(_next_data(page)), page_url)
        if job:
            yielded += 1
            yield job
