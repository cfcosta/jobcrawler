
import json
import html
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

API = "https://euremotejobs.com/wp-json/wp/v2"
SITE = API + "/job-listings?job-categories=65&per_page=100&orderby=date&order=desc"
# The job_listing_region taxonomy is euremotejobs' only authoritative geography
# ("Worldwide", "Europe", "Germany", ...); postings carry it as term ids, so we
# resolve the id->name map once per crawl and turn each job's ids into a
# geo-parseable location instead of collapsing every listing to a bare "Remote".
REGIONS = API + "/job_listing_region?per_page=100&orderby=count&order=desc&_fields=id,name"
EXTRA_HOSTS = []

FIELDS = "id,date,date_gmt,status,link,title.rendered,content.rendered,meta,job_listing_region"


def _fetch_text(ctx, url):
    res = ctx.fetch(url)
    if isinstance(res, str):
        return res
    if hasattr(res, "text"):
        return res.text
    if hasattr(res, "content"):
        data = res.content
        return data.decode("utf-8", "replace") if isinstance(data, (bytes, bytearray)) else str(data)
    return str(res)


def _with_query(url, **updates):
    parts = urlparse(url)
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    for key, value in updates.items():
        q[key] = str(value)
    return urlunparse((parts.scheme, parts.netloc, parts.path, parts.params, urlencode(q), parts.fragment))


def _text(value):
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("rendered")
    value = html.unescape(str(value)).strip()
    return value or None


def _region_names(ctx):
    """Map job_listing_region term id -> name, paging the taxonomy once per crawl.

    A failed or empty fetch yields an empty map so the crawl degrades to bare
    "Remote" locations rather than dropping every posting.
    """
    names = {}
    for page in range(1, 6):
        try:
            data = json.loads(_fetch_text(ctx, _with_query(REGIONS, page=page)))
        except Exception:
            break
        if not isinstance(data, list) or not data:
            break
        for term in data:
            tid, name = term.get("id"), _text(term.get("name"))
            if tid is not None and name:
                names[tid] = name
        if len(data) < 100:
            break
    return names


def _location(region_names, ids):
    """Compose a geo-parseable location from a posting's region term ids.

    Each region reads as a remote fence — "Remote - Germany" resolves to a DE
    fence, "Remote - Worldwide" to worldwide — and several regions join so the
    parser emits one fence apiece. No resolvable region falls back to a bare
    "Remote".
    """
    picked = [region_names.get(i) for i in (ids or [])]
    picked = [n for n in picked if n]
    if not picked:
        return "Remote"
    return "Remote - " + ", ".join(picked)


def crawl(ctx):
    region_names = _region_names(ctx)
    for page in range(1, 3):
        url = _with_query(SITE, page=page, per_page=100, _fields=FIELDS)
        data = json.loads(_fetch_text(ctx, url))
        if not isinstance(data, list) or not data:
            break
        for item in data:
            if item.get("status") and item.get("status") != "publish":
                continue
            meta = item.get("meta") or {}
            job = {
                "url": item.get("link"),
                "title": _text(item.get("title")),
                "company": _text(meta.get("_company_name")) or "EU Remote Jobs",
                "location": _location(region_names, item.get("job_listing_region")),
                # euremotejobs is a remote-only board; the per-post _remote_position
                # meta is unreliable (real remote roles ship it as 0), so every
                # listing is remote by construction.
                "workplace_type": "remote",
                "description": _text((item.get("content") or {}).get("rendered")),
                "source": SITE,
                "external_id": str(item.get("id")) if item.get("id") is not None else None,
                "posted_at": item.get("date_gmt") or item.get("date"),
            }
            currency = _text(meta.get("_job_salary_currency"))
            period = _text(meta.get("_job_salary_unit"))
            if currency:
                job["salary_currency"] = currency
            if period:
                job["salary_period"] = period
            job = {k: v for k, v in job.items() if v not in (None, "")}
            if job.get("url") and job.get("title") and job.get("description"):
                yield job
