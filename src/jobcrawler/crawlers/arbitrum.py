
import json
import re
import html
from urllib.parse import urljoin

SITE = "https://jobs.arbitrum.io/"
EXTRA_HOSTS = []
MAX_JOBS = 6


def _response_text(resp):
    if resp is None:
        return ""
    if isinstance(resp, str):
        return resp
    if hasattr(resp, "text"):
        return resp.text
    return str(resp)


def _fetch(ctx, url):
    try:
        return _response_text(ctx.fetch(url))
    except TypeError:
        return _response_text(ctx.fetch(url, "GET", ""))


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = html.unescape(value).strip()
        return value or None
    return value


def _strip_html(value):
    if not value:
        return None
    text = str(value)
    text = re.sub(r"(?is)<\s*(script|style)\b.*?</\s*\1\s*>", " ", text)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|tr|h1|h2|h3|h4|ul|ol)\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li\b[^>]*>", "\n- ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _first_text(value):
    if isinstance(value, dict):
        for key in ("name", "label", "title", "value"):
            found = _clean(value.get(key))
            if found:
                return found
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
    match = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', page, re.I | re.S)
    if not match:
        return None
    raw = match.group(1)
    try:
        return json.loads(html.unescape(raw))
    except Exception:
        try:
            return json.loads(raw)
        except Exception:
            return None


def _sitemap_job_urls(sitemap):
    urls = []
    for block in re.findall(r"<url\b[^>]*>(.*?)</url>", sitemap, re.I | re.S):
        loc_match = re.search(r"<loc\b[^>]*>(.*?)</loc>", block, re.I | re.S)
        if not loc_match:
            continue
        loc = html.unescape(loc_match.group(1)).strip()
        if re.search(r"/companies/[^/]+/jobs/[^/?#]+", loc):
            lm_match = re.search(r"<lastmod\b[^>]*>(.*?)</lastmod>", block, re.I | re.S)
            lastmod = lm_match.group(1).strip() if lm_match else ""
            urls.append((lastmod, loc))
    urls.sort(reverse=True)
    seen = set()
    for row in urls:
        url = row[1]
        if url not in seen:
            seen.add(url)
            yield url


def _money_from_cents(value):
    if value is None:
        return None
    try:
        amount = int(value) / 100.0
    except Exception:
        return None
    if amount.is_integer():
        return str(int(amount))
    return ("%.2f" % amount).rstrip("0").rstrip(".")


def _salary(job):
    if not job.get("compensationPublic"):
        return None
    low = _money_from_cents(job.get("compensationAmountMinCents"))
    high = _money_from_cents(job.get("compensationAmountMaxCents"))
    currency = _clean(job.get("compensationCurrency"))
    period = _clean(job.get("compensationPeriod"))
    if period == "period_not_defined":
        period = None
    if not low and not high:
        return None
    if low and high and low != high:
        text = low + " - " + high
    else:
        text = low or high
    if currency:
        text = currency + " " + text
    if period:
        text = text + " / " + period
    return text


def _apply_url(job, page_url):
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


def _job_from_current(job, page_url):
    if not isinstance(job, dict):
        return None
    status = _clean(job.get("status"))
    if status and status.lower() not in ("active", "published", "open"):
        return None
    if job.get("deactivatedAt") or job.get("closedAt"):
        return None

    title = _clean(job.get("title"))
    description = _strip_html(job.get("description"))
    organization = job.get("organization") if isinstance(job.get("organization"), dict) else {}
    company = _clean(organization.get("name")) or "Arbitrum"
    location = _join_names(job.get("locations")) or "Remote"
    if not (title and description and company and location):
        return None

    out = {
        "url": page_url,
        "title": title,
        "company": company,
        "location": location,
        "description": description,
        "source": SITE,
    }

    external_id = _clean(job.get("id"))
    if external_id:
        out["external_id"] = str(external_id)

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

    apply_url = _apply_url(job, page_url)
    if apply_url:
        out["apply_url"] = apply_url

    return out


def crawl(ctx):
    sitemap = _fetch(ctx, urljoin(SITE, "sitemap.xml"))
    yielded = 0
    for url in _sitemap_job_urls(sitemap):
        if yielded >= MAX_JOBS:
            break
        page = _fetch(ctx, url)
        data = _next_data(page)
        if not data:
            continue
        state = data.get("props", {}).get("pageProps", {}).get("initialState", {})
        jobs = state.get("jobs", {}) if isinstance(state, dict) else {}
        current = jobs.get("currentJob") if isinstance(jobs, dict) else None
        item = _job_from_current(current, url)
        if item:
            yielded += 1
            yield item
