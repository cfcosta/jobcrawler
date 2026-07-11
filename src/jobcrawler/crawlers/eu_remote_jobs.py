
import json
import html
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

SITE = "https://euremotejobs.com/wp-json/wp/v2/job-listings?job-categories=65&per_page=100&orderby=date&order=desc"
EXTRA_HOSTS = []

FIELDS = "id,date,date_gmt,status,link,title.rendered,content.rendered,meta"


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


def crawl(ctx):
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
                "location": "Remote",
                "description": _text((item.get("content") or {}).get("rendered")),
                "source": SITE,
                "external_id": str(item.get("id")) if item.get("id") is not None else None,
                "posted_at": item.get("date_gmt") or item.get("date"),
            }
            if meta.get("_remote_position") in (1, "1", True, "true", "yes"):
                job["workplace_type"] = "remote"
            currency = _text(meta.get("_job_salary_currency"))
            period = _text(meta.get("_job_salary_unit"))
            if currency:
                job["salary_currency"] = currency
            if period:
                job["salary_period"] = period
            job = {k: v for k, v in job.items() if v not in (None, "")}
            if job.get("url") and job.get("title") and job.get("description"):
                yield job
