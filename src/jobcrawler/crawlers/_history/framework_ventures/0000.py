
import json
import re
import html
from urllib.parse import urlparse

SITE = "https://jobs.framework.ventures/jobs"
EXTRA_HOSTS = []


def _fetch(ctx, url):
    try:
        return ctx.fetch(url)
    except TypeError:
        return ctx.fetch(url, "GET", "")


def _clean_text(value):
    if value is None:
        return None
    value = html.unescape(str(value))
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</p\s*>", "\n\n", value)
    value = re.sub(r"(?i)</li\s*>", "\n", value)
    value = re.sub(r"(?i)<li[^>]*>", "- ", value)
    value = re.sub(r"<script\b.*?</script>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<style\b.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _first_nonempty(*values):
    for value in values:
        if isinstance(value, str):
            value = value.strip()
        if value not in (None, "", [], {}):
            return value
    return None


def _make_job(**fields):
    return {k: v for k, v in fields.items() if v not in (None, "", [], {})}


def _names(items):
    out = []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, str):
                name = item.strip()
            elif isinstance(item, dict):
                name = _first_nonempty(item.get("name"), item.get("description"), item.get("label"))
            else:
                name = None
            if name:
                out.append(name)
    return out


def _salary(job):
    if not job.get("compensationPublic"):
        return None
    currency = job.get("compensationCurrency")
    period = job.get("compensationPeriod")
    min_cents = job.get("compensationAmountMinCents")
    max_cents = job.get("compensationAmountMaxCents")
    if not currency or (min_cents is None and max_cents is None):
        return None

    def money(cents):
        if cents is None:
            return None
        amount = cents / 100
        if amount.is_integer():
            amount = int(amount)
        return f"{currency} {amount:,}"

    low = money(min_cents)
    high = money(max_cents)
    text = f"{low} - {high}" if low and high and low != high else (low or high)
    if period and period != "period_not_defined":
        text = f"{text} / {period.replace('period_', '')}"
    return text


def _extract_from_next_json(text, page_url):
    data = json.loads(text)
    job = (
        data.get("pageProps", {})
        .get("initialState", {})
        .get("jobs", {})
        .get("currentJob", {})
    )
    if not job:
        return None

    org = job.get("organization") or {}
    locations = _names(job.get("locations"))
    departments = _names(job.get("jobFunctions"))
    employment_types = _names(job.get("employmentTypes"))

    return _make_job(
        url=page_url,
        title=job.get("title"),
        company=org.get("name"),
        location=", ".join(locations) if locations else None,
        description=_clean_text(job.get("description")),
        source=SITE,
        department=", ".join(departments) if departments else None,
        employment_type=", ".join(employment_types) if employment_types else None,
        salary=_salary(job),
        posted_at=job.get("postedAt"),
        expires_at=job.get("expiresAt"),
        external_id=str(job.get("id")) if job.get("id") is not None else None,
        apply_url=job.get("url"),
    )


def _extract_jsonld(doc, page_url):
    pattern = r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>'
    for raw in re.findall(pattern, doc, re.I | re.S):
        try:
            data = json.loads(html.unescape(raw))
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict) or item.get("@type") != "JobPosting":
                continue
            org = item.get("hiringOrganization") or {}
            loc = item.get("jobLocation") or {}
            loc_name = None
            if isinstance(loc, dict):
                addr = loc.get("address") if isinstance(loc.get("address"), dict) else loc
                loc_name = _first_nonempty(addr.get("addressLocality"), addr.get("addressRegion"), addr.get("addressCountry"))
            emp = item.get("employmentType")
            if isinstance(emp, list):
                emp = ", ".join(str(x) for x in emp if x)
            return _make_job(
                url=page_url,
                title=item.get("title"),
                company=org.get("name") if isinstance(org, dict) else None,
                location=loc_name,
                description=_clean_text(item.get("description")),
                source=SITE,
                employment_type=emp,
                posted_at=item.get("datePosted"),
                expires_at=item.get("validThrough"),
            )
    return None


def _next_data(doc):
    match = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', doc, re.S)
    if not match:
        return {}
    try:
        return json.loads(html.unescape(match.group(1)))
    except Exception:
        return {}


def _build_id(doc, data):
    build_id = data.get("buildId")
    if build_id:
        return build_id
    match = re.search(r"/_next/static/([^/]+)/_buildManifest\.js", doc)
    if match:
        return match.group(1)
    return None


def _initial_jobs(data):
    jobs = (
        data.get("props", {})
        .get("pageProps", {})
        .get("initialState", {})
        .get("jobs", {})
        .get("found", [])
    )
    if isinstance(jobs, list):
        return jobs
    return []


def _page_url_from_listing(item):
    org = item.get("organization") or {}
    org_slug = org.get("slug")
    job_slug = item.get("slug")
    if org_slug and job_slug:
        return f"https://jobs.framework.ventures/companies/{org_slug}/jobs/{job_slug}"
    return None


def crawl(ctx):
    doc = _fetch(ctx, SITE)
    data = _next_data(doc)
    build_id = _build_id(doc, data)
    yielded = 0

    for item in _initial_jobs(data):
        page_url = _page_url_from_listing(item)
        if not page_url:
            continue

        job = None
        if build_id:
            path = urlparse(page_url).path
            data_url = f"https://jobs.framework.ventures/_next/data/{build_id}{path}.json"
            try:
                job = _extract_from_next_json(_fetch(ctx, data_url), page_url)
            except Exception:
                job = None

        if job is None:
            try:
                job = _extract_jsonld(_fetch(ctx, page_url), page_url)
            except Exception:
                job = None

        if job is not None:
            yield job
            yielded += 1

        if yielded >= 8:
            break
