
import json
import re
import html

SITE = "https://boards-api.greenhouse.io/v1/boards/tines/jobs?content=true"
EXTRA_HOSTS = []

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

def _text_from_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|ul|ol)\s*>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    return text or None

def _fetch_text(ctx, url):
    response = ctx.fetch(url)
    if hasattr(response, "text"):
        return response.text
    return response

def _names(items):
    out = []
    for item in items or []:
        if isinstance(item, dict):
            name = item.get("name")
            if name:
                out.append(name)
    return out

def crawl(ctx):
    data = json.loads(_fetch_text(ctx, SITE))
    for job in data.get("jobs", []):
        title = job.get("title")
        url = job.get("absolute_url")
        description = _text_from_html(job.get("content"))
        location_obj = job.get("location") or {}
        location = location_obj.get("name") if isinstance(location_obj, dict) else None
        if not (title and url and description and location):
            continue

        departments = _names(job.get("departments"))
        offices = _names(job.get("offices"))

        item = {
            "url": url,
            "title": title,
            "company": "Tines",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(job.get("id")),
            "apply_url": url,
        }

        if offices:
            item["locations"] = offices
        if departments:
            item["department"] = departments[0]
        if job.get("first_published"):
            item["posted_at"] = job.get("first_published")

        ranges = job.get("pay_input_ranges") or []
        currencies = {r.get("currency_type") for r in ranges if r.get("currency_type")}
        if len(currencies) == 1 and ranges:
            mins = [r.get("min_cents") for r in ranges if isinstance(r.get("min_cents"), (int, float))]
            maxs = [r.get("max_cents") for r in ranges if isinstance(r.get("max_cents"), (int, float))]
            if mins:
                item["salary_min"] = min(mins) / 100
            if maxs:
                item["salary_max"] = max(maxs) / 100
            item["salary_currency"] = next(iter(currencies))

        yield item
