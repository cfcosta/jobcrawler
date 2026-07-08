
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/stone/jobs?content=true"
EXTRA_HOSTS = []

COMPANY = "Stone"

_BR_RE = re.compile(r"(?i)<\s*br\s*/?\s*>")
_BLOCK_RE = re.compile(r"(?i)</\s*(?:p|div|li|h[1-6]|section|blockquote|tr|ul|ol)\s*>")
_TAG_RE = re.compile(r"<[^>]+>")
_INLINE_WS_RE = re.compile(r"[ \t\r\f\v]+")
_LEAD_WS_RE = re.compile(r" *\n *")
_BLANK_RE = re.compile(r"\n{3,}")


def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = _BR_RE.sub("\n", text)
    text = _BLOCK_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _INLINE_WS_RE.sub(" ", text)
    text = _LEAD_WS_RE.sub("\n", text)
    text = _BLANK_RE.sub("\n\n", text)
    return text.strip() or None


def _text(value):
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _departments(items):
    if not isinstance(items, list):
        return None
    names = [
        d.get("name").strip()
        for d in items
        if isinstance(d, dict) and isinstance(d.get("name"), str) and d.get("name").strip()
    ]
    return "; ".join(names) if names else None


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        loc = job.get("location")
        location = _text(loc.get("name")) if isinstance(loc, dict) else None
        title = _text(job.get("title"))
        description = _clean_html(job.get("content"))
        url = _text(job.get("absolute_url"))
        if not (title and location and description and url):
            continue

        job_id = job.get("id")
        item = {
            "url": url,
            "title": title,
            "company": COMPANY,
            "location": location,
            "description": description,
            "source": "stone",
            "external_id": str(job_id) if job_id is not None else None,
            "apply_url": url,
            "posted_at": _text(job.get("first_published")),
            "expires_at": _text(job.get("application_deadline")),
            "department": _departments(job.get("departments")),
        }
        yield {k: v for k, v in item.items() if v not in (None, "")}
