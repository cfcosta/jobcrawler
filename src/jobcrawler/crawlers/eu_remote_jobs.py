
import json
import re
import html
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

SITE = "https://euremotejobs.com/wp-json/wp/v2/job-listings?job-categories=65&per_page=100&orderby=date&order=desc"
EXTRA_HOSTS = []

_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_RE = re.compile(r"(?i)</\s*(p|div|li|h[1-6]|tr|section|article|ul|ol|blockquote)\s*>")
_BR_RE = re.compile(r"(?i)<\s*br\s*/?\s*>")

def _clean_html(value):
    if not value:
        return None
    value = html.unescape(str(value))
    value = _BR_RE.sub("\n", value)
    value = _BLOCK_RE.sub("\n", value)
    value = _TAG_RE.sub(" ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = value.strip()
    return value or None

def _with_query(url, **params):
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    for key, value in params.items():
        query[key] = str(value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

def _remote_location_from_title(title):
    # The board is explicitly EU Remote Jobs; many titles end with a country qualifier.
    if title and " - " in title:
        tail = title.rsplit(" - ", 1)[-1].strip()
        if 2 <= len(tail) <= 60:
            return tail
    return "Remote"

def crawl(ctx):
    fields = "id,date,date_gmt,link,title,content,meta,status"
    url = _with_query(SITE, per_page=100, page=1, _fields=fields)
    data = json.loads(ctx.fetch(url))

    for item in data:
        if item.get("status") and item.get("status") != "publish":
            continue

        title = _clean_html((item.get("title") or {}).get("rendered"))
        description = _clean_html((item.get("content") or {}).get("rendered"))
        job_url = item.get("link")
        meta = item.get("meta") or {}
        company = _clean_html(meta.get("_company_name")) or "EU Remote Jobs"

        if not (title and description and job_url):
            continue

        job = {
            "url": job_url,
            "title": title,
            "company": company,
            "location": _remote_location_from_title(title),
            "description": description,
            "external_id": str(item.get("id")),
            "source": SITE,
        }

        posted_at = item.get("date_gmt") or item.get("date")
        if posted_at:
            job["posted_at"] = posted_at

        currency = meta.get("_job_salary_currency")
        period = meta.get("_job_salary_unit")
        if currency:
            job["salary_currency"] = currency
        if period:
            job["salary_period"] = period

        yield job
