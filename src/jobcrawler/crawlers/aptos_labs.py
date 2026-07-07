
import json
import html
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/aptoslabs/jobs?content=true"

_BLOCK_TAG_RE = re.compile(r"(?i)</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|fieldset|figcaption|figure|footer|form|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_NEWLINES_RE = re.compile(r"\n\s*\n\s*\n+")

def _clean(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None

def _html_to_text(value):
    if not value:
        return None
    # Greenhouse content is escaped HTML; unescape before stripping markup.
    text = html.unescape(value)
    text = _BLOCK_TAG_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = _NEWLINES_RE.sub("\n\n", text)
    return text.strip() or None

def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for item in data.get("jobs", []):
        location_obj = item.get("location") or {}
        if isinstance(location_obj, dict):
            location = _clean(location_obj.get("name"))
        else:
            location = _clean(location_obj)

        job = {
            "url": _clean(item.get("absolute_url")),
            "title": _clean(item.get("title")),
            "company": "Aptos Labs",
            "location": location,
            "description": _html_to_text(item.get("content")),
            "source": SITE,
        }

        external_id = item.get("id")
        if external_id is not None:
            job["external_id"] = str(external_id)

        yield {k: v for k, v in job.items() if v is not None}
