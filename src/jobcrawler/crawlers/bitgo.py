import json
import re
import html

SITE = 'https://boards-api.greenhouse.io/v1/boards/bitgo/jobs?content=true'
EXTRA_HOSTS = []

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

def _text_from_greenhouse_content(value):
    if not value:
        return None
    # Greenhouse escapes the full HTML body; decode before removing markup.
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|ul|ol|section|article)\s*>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    return text or None

def _response_text(response):
    return response.text if hasattr(response, "text") else response

def crawl(ctx):
    raw = _response_text(ctx.fetch(SITE))
    data = json.loads(raw)
    for item in data.get("jobs", []):
        title = (item.get("title") or "").strip()
        url = (item.get("absolute_url") or "").strip()
        location = ((item.get("location") or {}).get("name") or "").strip()
        description = _text_from_greenhouse_content(item.get("content"))
        if not (title and url and location and description):
            continue

        job = {
            "url": url,
            "title": title,
            "company": "BitGo",
            "location": location,
            "description": description,
            "source": SITE,
        }

        external_id = item.get("id")
        if external_id is not None:
            job["external_id"] = str(external_id)

        yield job
