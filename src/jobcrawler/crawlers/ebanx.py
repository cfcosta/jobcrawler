
import json
import re
import html

SITE = "https://boards-api.greenhouse.io/v1/boards/ebanx/jobs?content=true"
EXTRA_HOSTS = []


def _clean_description(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</?(p|div|li|ul|ol|h[1-6]|blockquote|section|article)[^>]*>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _non_empty(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def crawl(ctx):
    raw = ctx.fetch(SITE)
    data = json.loads(raw)
    for item in data.get("jobs", []):
        title = _non_empty(item.get("title"))
        url = _non_empty(item.get("absolute_url"))
        description = _clean_description(item.get("content"))
        loc = item.get("location") or {}
        location = _non_empty(loc.get("name") if isinstance(loc, dict) else loc)

        if not (title and url and location and description):
            continue

        external_id = item.get("id") or item.get("internal_job_id")
        job = {
            "url": url,
            "title": title,
            "company": "EBANX",
            "location": location,
            "description": description,
            "source": SITE,
        }
        if external_id is not None:
            job["external_id"] = str(external_id)
        if _non_empty(item.get("updated_at")):
            job["posted_at"] = _non_empty(item.get("updated_at"))
        yield job
