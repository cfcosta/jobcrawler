import html
import json
import re
from urllib.parse import urljoin

SITE = "https://coinbase.getro.com/"
EXTRA_HOSTS = []

MAX_DETAILS = 2

_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', re.I | re.S
)

_SCRIPT_RE = re.compile(r"(?is)<\s*(script|style)\b.*?</\s*\1\s*>")
_BR_RE = re.compile(r"(?i)<\s*br\s*/?\s*>")
_BLOCK_CLOSE_RE = re.compile(r"(?i)</\s*(p|div|li|tr|h[1-6]|ul|ol|section)\s*>")
_LI_OPEN_RE = re.compile(r"(?i)<\s*li\b[^>]*>")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_NL_WS_RE = re.compile(r" *\n *")
_MULTI_NL_RE = re.compile(r"\n{3,}")

_ACTIVE = {"active", "published", "open", "live"}


def _clean(value):
    if isinstance(value, str):
        value = html.unescape(value).strip()
        return value or None
    return value


def _strip_html(value):
    if not value:
        return None
    text = _SCRIPT_RE.sub(" ", str(value))
    text = _BR_RE.sub("\n", text)
    text = _BLOCK_CLOSE_RE.sub("\n", text)
    text = _LI_OPEN_RE.sub("\n- ", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = _NL_WS_RE.sub("\n", text)
    text = _MULTI_NL_RE.sub("\n\n", text)
    return text.strip() or None


def _join_names(items):
    names = []
    if isinstance(items, list):
        for item in items:
            name = _clean(item.get("name")) if isinstance(item, dict) else _clean(item)
            if name and name not in names:
                names.append(name)
    return ", ".join(names) or None


def _next_data(page):
    match = _NEXT_DATA_RE.search(page or "")
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except ValueError:
        return None


def _next_data_or_json(text):
    if not text:
        return None
    stripped = text.lstrip()
    if stripped[:1] in ("{", "["):
        try:
            return json.loads(stripped)
        except ValueError:
            return None
    return _next_data(text)


def _page_props(data):
    props = data.get("props")
    if isinstance(props, dict) and isinstance(props.get("pageProps"), dict):
        return props["pageProps"]
    page_props = data.get("pageProps")
    return page_props if isinstance(page_props, dict) else {}


def _jobs_state(data):
    state = _page_props(data).get("initialState") or {}
    jobs = state.get("jobs") if isinstance(state, dict) else None
    return jobs if isinstance(jobs, dict) else {}


def _slugs(entry):
    if not isinstance(entry, dict):
        return None
    organization = entry.get("organization")
    org_slug = (
        _clean(organization.get("slug")) if isinstance(organization, dict) else None
    )
    job_slug = _clean(entry.get("slug"))
    if org_slug and job_slug:
        return org_slug, job_slug
    return None


def _money(cents):
    try:
        return round(int(cents) / 100.0, 2)
    except (TypeError, ValueError):
        return None


def _salary(job):
    if not job.get("compensationPublic"):
        return {}
    low = _money(job.get("compensationAmountMinCents"))
    high = _money(job.get("compensationAmountMaxCents"))
    if low is None and high is None:
        return {}
    out = {}
    if low is not None:
        out["salary_min"] = low
    if high is not None:
        out["salary_max"] = high
    currency = _clean(job.get("compensationCurrency"))
    if currency:
        out["salary_currency"] = currency
    period = _clean(job.get("compensationPeriod"))
    if period and period != "period_not_defined":
        out["salary_period"] = period
    return out


def _apply_url(job):
    path = _clean(job.get("applicationPath"))
    if path:
        return urljoin(SITE, path)
    return _clean(job.get("url"))


def _build(job, page_url):
    if not isinstance(job, dict):
        return None
    status = _clean(job.get("status"))
    if status and status.lower() not in _ACTIVE:
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

    external_id = job.get("id")
    item = {
        "url": page_url,
        "title": title,
        "company": company,
        "location": location,
        "description": description,
        "source": "coinbase_portfolio",
        "external_id": str(external_id) if external_id is not None else None,
        "department": _join_names(job.get("jobFunctions")),
        "employment_type": _join_names(job.get("employmentTypes")),
        "posted_at": _clean(job.get("postedAt")),
        "expires_at": _clean(job.get("expiresAt")),
        "apply_url": _apply_url(job),
    }
    item.update(_salary(job))
    return {k: v for k, v in item.items() if v not in (None, "")}


def crawl(ctx):
    listing = _next_data(ctx.fetch(urljoin(SITE, "jobs")))
    if not listing:
        return
    found = _jobs_state(listing).get("found")
    if not isinstance(found, list):
        return
    build_id = _clean(listing.get("buildId"))

    fetched = 0
    seen = set()
    for entry in found:
        if fetched >= MAX_DETAILS:
            break
        slugs = _slugs(entry)
        if not slugs:
            continue
        org_slug, job_slug = slugs
        page_url = urljoin(SITE, "companies/" + org_slug + "/jobs/" + job_slug)
        if page_url in seen:
            continue
        seen.add(page_url)
        if build_id:
            fetch_url = urljoin(
                SITE,
                "_next/data/"
                + build_id
                + "/companies/"
                + org_slug
                + "/jobs/"
                + job_slug
                + ".json",
            )
        else:
            fetch_url = page_url
        fetched += 1
        data = _next_data_or_json(ctx.fetch(fetch_url))
        if not data:
            continue
        item = _build(_jobs_state(data).get("currentJob"), page_url)
        if item:
            yield item
