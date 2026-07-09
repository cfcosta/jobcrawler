
import json
import re
import html

SITE = 'https://boards-api.greenhouse.io/v1/boards/okta/jobs?content=true'
EXTRA_HOSTS = []

_TAG_BREAK_RE = re.compile(r"(?i)</?(?:p|div|br|li|ul|ol|h[1-6]|section|tr|table)[^>]*>")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_NL_RE = re.compile(r"\n\s*\n+")

def _clean_description(value):
    if value is None:
        return None
    # Greenhouse returns full posting HTML with entities escaped.
    text = html.unescape(str(value))
    text = _TAG_BREAK_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = _NL_RE.sub("\n\n", text)
    return text.strip() or None

def _location_name(job):
    loc = job.get("location")
    if isinstance(loc, dict):
        name = loc.get("name")
        if name:
            return str(name).strip() or None
    if isinstance(loc, str):
        return loc.strip() or None
    return None

def crawl(ctx):
    payload = ctx.fetch(SITE)
    data = json.loads(payload)
    for job in data.get("jobs", []):
        title = str(job.get("title") or "").strip()
        url = str(job.get("absolute_url") or "").strip()
        location = _location_name(job)
        description = _clean_description(job.get("content"))
        if not (title and url and location and description):
            continue

        item = {
            "url": url,
            "title": title,
            "company": "Okta",
            "location": location,
            "description": description,
            "source": SITE,
        }
        if job.get("id") is not None:
            item["external_id"] = str(job.get("id"))
        if url:
            item["apply_url"] = url
        yield item
