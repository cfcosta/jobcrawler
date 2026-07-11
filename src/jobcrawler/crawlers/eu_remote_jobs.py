
import html
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SITE = "https://euremotejobs.com/wp-json/wp/v2/job-listings?job-categories=65&per_page=100&orderby=date&order=desc"
EXTRA_HOSTS = []

_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_RE = re.compile(r"(?i)<br\s*/?>|</(?:p|div|li|h[1-6])>")
_WS_RE = re.compile(r"\s+")

def _text(v):
    if v is None:
        return None
    s = html.unescape(str(v))
    s = _BLOCK_RE.sub("\n", s)
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = _WS_RE.sub(" ", s).strip()
    return s or None

def _api_url(page):
    parts = urlsplit(SITE)
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    q["page"] = str(page)
    q["per_page"] = "100"
    q["_fields"] = "id,date,status,link,title.rendered,content.rendered,meta"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))

def crawl(ctx):
    rows = json.loads(ctx.fetch(_api_url(1)))
    for row in rows:
        if row.get("status") and row.get("status") != "publish":
            continue
        title = _text((row.get("title") or {}).get("rendered"))
        desc = _text((row.get("content") or {}).get("rendered"))
        url = row.get("link")
        if not title or not desc or not url:
            continue
        meta = row.get("meta") or {}
        company = _text(meta.get("_company_name")) or "EU Remote Jobs"
        payload = {
            "url": url,
            "title": title,
            "company": company,
            "location": "Remote",
            "description": desc,
            "source": SITE,
            "posted_at": row.get("date"),
            "external_id": str(row.get("id")),
        }
        if meta.get("_remote_position") in (1, "1", True):
            payload["workplace_type"] = "remote"
        yield payload
