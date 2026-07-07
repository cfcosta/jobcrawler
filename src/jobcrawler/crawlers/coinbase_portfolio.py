from __future__ import annotations

import html
import json
import re
from urllib.parse import urljoin

SITE = "https://coinbase.getro.com/"
EXTRA_HOSTS = []

SOURCE = "coinbase_portfolio"
MAX_DETAILS = 3

_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', re.I | re.S
)
_HTTP_RE = re.compile(r"(?i)^https?://")

_SCRIPT_RE = re.compile(r"(?is)<\s*(script|style)\b.*?</\s*\1\s*>")
_BR_RE = re.compile(r"(?i)<\s*br\s*/?\s*>")
_BLOCK_CLOSE_RE = re.compile(r"(?i)</\s*(p|div|li|tr|h[1-6]|ul|ol|section)\s*>")
_LI_OPEN_RE = re.compile(r"(?i)<\s*li\b[^>]*>")
_TAG_RE = re.compile(r"(?s)<[^>]+>")
_SPACE_RE = re.compile(r"[ \t\r\f\v]+")
_NL_WS_RE = re.compile(r" *\n *")
_NL_RUN_RE = re.compile(r"\n{3,}")

_ACTIVE = {"active", "published", "open", "live"}


def _clean(value):
    if isinstance(value, str):
        value = html.unescape(value).strip()
        return value or None
    return None


def _strip_html(value):
    if not value:
        return None
    text = str(value)
    text = _SCRIPT_RE.sub(" ", text)
    text = _BR_RE.sub("\n", text)
    text = _BLOCK_CLOSE_RE.sub("\n", text)
    text = _LI_OPEN_RE.sub("\n- ", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _SPACE_RE.sub(" ", text)
    text = _NL_WS_RE.sub("\n", text)
    text = _NL_RUN_RE.sub("\n\n", text)
    return text.strip() or None


def _name(value):
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
            name = _name(item)
            if name:
                names.append(name)
    else:
        name = _name(items)
        if name:
            names.append(name)
    return ", ".join(dict.fromkeys(names)) or None


def _next_data(page):
    match = _NEXT_DATA_RE.search(page)
    if not match:
        return None
    raw = match.group(1)
    try:
        return json.loads(html.unescape(raw))
    except ValueError:
        try:
            return json.loads(raw)
        except ValueError:
            return None


def _page_props(data):
    if not isinstance(data, dict):
        return None
    props = data.get("props")
    if isinstance(props, dict) and isinstance(props.get("pageProps"), dict):
        return props["pageProps"]
    page_props = data.get("pageProps")
    return page_props if isinstance(page_props, dict) else None


def _jobs_state(data):
    page_props = _page_props(data)
    state = page_props.get("initialState") if isinstance(page_props, dict) else None
    jobs = state.get("jobs") if isinstance(state, dict) else None
    return jobs if isinstance(jobs, dict) else None


def _parse_detail(text):
    if not text:
        return None
    stripped = text.lstrip()
    if stripped[:1] == "{":
        try:
            return json.loads(stripped)
        except ValueError:
            return None
    return _next_data(text)


def _found(data):
    jobs = _jobs_state(data)
    found = jobs.get("found") if isinstance(jobs, dict) else None
    return found if isinstance(found, list) else []


def _current_job(data):
    jobs = _jobs_state(data)
    current = jobs.get("currentJob") if isinstance(jobs, dict) else None
    return current if isinstance(current, dict) else None


def _slugs(entry):
    if not isinstance(entry, dict):
        return None
    organization = entry.get("organization")
    org_slug = (
        _clean(organization.get("slug")) if isinstance(organization, dict) else None
    )
    job_slug = _clean(entry.get("slug"))
    if not (org_slug and job_slug):
        return None
    return org_slug, job_slug


def _detail_url(org_slug, job_slug):
    return urljoin(SITE, f"companies/{org_slug}/jobs/{job_slug}")


def _data_url(org_slug, job_slug, build_id):
    if not build_id:
        return None
    return urljoin(
        SITE, f"_next/data/{build_id}/companies/{org_slug}/jobs/{job_slug}.json"
    )


def _dollars(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    amount = value / 100.0
    return amount if amount > 0 else None


def _money_text(value):
    amount = _dollars(value)
    if amount is None:
        return None
    if amount.is_integer():
        return str(int(amount))
    return f"{amount:.2f}".rstrip("0").rstrip(".")


def _salary_fields(job):
    if not job.get("compensationPublic"):
        return {}
    low = _money_text(job.get("compensationAmountMinCents"))
    high = _money_text(job.get("compensationAmountMaxCents"))
    if not low and not high:
        return {}
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
    fields = {"salary": text}
    min_value = _dollars(job.get("compensationAmountMinCents"))
    max_value = _dollars(job.get("compensationAmountMaxCents"))
    if min_value is not None:
        fields["salary_min"] = min_value
    if max_value is not None:
        fields["salary_max"] = max_value
    if currency:
        fields["salary_currency"] = currency
    if period:
        fields["salary_period"] = period
    return fields


def _apply_url(job):
    external = _clean(job.get("url"))
    if external and _HTTP_RE.match(external):
        return external
    path = _clean(job.get("applicationPath"))
    if path:
        return urljoin(SITE, path)
    source = job.get("source")
    if isinstance(source, dict):
        for key in ("url", "jobUrl", "applyUrl", "applicationUrl"):
            value = _clean(source.get(key))
            if value:
                return value
    return None


def _build(job, page_url):
    status = _clean(job.get("status"))
    if status and status.lower() not in _ACTIVE:
        return None
    if job.get("deactivatedAt") or job.get("closedAt"):
        return None
    if _clean(job.get("visibility")) == "hidden":
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

    item = {
        "url": page_url,
        "title": title,
        "company": company,
        "location": location,
        "description": description,
        "source": SOURCE,
        "employment_type": _join_names(job.get("employmentTypes")),
        "department": _join_names(job.get("jobFunctions")),
        "posted_at": _clean(job.get("postedAt")),
        "expires_at": _clean(job.get("expiresAt")),
        "apply_url": _apply_url(job),
    }

    identifier = job.get("id")
    if identifier is not None:
        item["external_id"] = str(identifier)

    item.update(_salary_fields(job))
    return {k: v for k, v in item.items() if v not in (None, "")}


def crawl(ctx):
    listing = _next_data(ctx.fetch(urljoin(SITE, "jobs")))
    build_id = _clean(listing.get("buildId")) if isinstance(listing, dict) else None
    fetches = 0
    seen = set()
    for entry in _found(listing):
        if fetches >= MAX_DETAILS:
            break
        slugs = _slugs(entry)
        if not slugs:
            continue
        org_slug, job_slug = slugs
        page_url = _detail_url(org_slug, job_slug)
        if page_url in seen:
            continue
        seen.add(page_url)
        fetches += 1
        try:
            text = ctx.fetch(_data_url(org_slug, job_slug, build_id) or page_url)
        except Exception:
            continue
        current = _current_job(_parse_detail(text))
        if not current:
            continue
        item = _build(current, page_url)
        if item:
            yield item
