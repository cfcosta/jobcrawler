
import json
import re
import html

SITE = 'https://boards-api.greenhouse.io/v1/boards/adyen/jobs?content=true'

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")

def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|tr|section|article)\s*>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    lines = []
    for line in text.splitlines():
        line = _WS_RE.sub(" ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines) or None

def _flatten_names(items):
    names = []
    for item in items or []:
        if isinstance(item, dict) and item.get("name"):
            names.append(str(item["name"]).strip())
    return ", ".join(n for n in names if n) or None

def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for raw in data.get("jobs", []):
        title = raw.get("title")
        url = raw.get("absolute_url")
        loc = raw.get("location") or {}
        location = loc.get("name") if isinstance(loc, dict) else None
        description = _clean_html(raw.get("content"))
        if not (title and url and location and description):
            continue

        job = {
            "url": url,
            "title": title,
            "company": "Adyen",
            "location": location,
            "description": description,
            "source": SITE,
        }

        external_id = raw.get("id")
        if external_id is not None:
            job["external_id"] = str(external_id)

        department = _flatten_names(raw.get("departments"))
        if department:
            job["department"] = department

        apply_url = raw.get("absolute_url")
        if apply_url:
            job["apply_url"] = apply_url

        yield job
