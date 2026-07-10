
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/xpinc/jobs?content=true"
EXTRA_HOSTS = []

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_RE = re.compile(r"\n\s*\n+")

def _text_from_greenhouse_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|tr)\s*>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = _BLANK_RE.sub("\n\n", text)
    return text.strip() or None

def _names(items):
    out = []
    for item in items or []:
        if isinstance(item, dict) and item.get("name"):
            out.append(item["name"])
    return out

def _salary_fields(job):
    ranges = job.get("pay_input_ranges") or []
    if not ranges:
        return {}
    currencies = {r.get("currency_type") for r in ranges if r.get("currency_type")}
    if len(currencies) != 1:
        return {}
    mins = [r.get("min_cents") for r in ranges if isinstance(r.get("min_cents"), (int, float))]
    maxs = [r.get("max_cents") for r in ranges if isinstance(r.get("max_cents"), (int, float))]
    fields = {"salary_currency": currencies.pop()}
    if mins:
        fields["salary_min"] = min(mins) / 100
    if maxs:
        fields["salary_max"] = max(maxs) / 100
    return fields

def crawl(ctx):
    url = SITE + "&pay_transparency=true"
    data = json.loads(ctx.fetch(url))
    for job in data.get("jobs", []):
        title = job.get("title")
        location = (job.get("location") or {}).get("name")
        description = _text_from_greenhouse_html(job.get("content"))
        absolute_url = job.get("absolute_url")
        if not (title and location and description and absolute_url):
            continue

        departments = _names(job.get("departments"))
        offices = _names(job.get("offices"))

        item = {
            "url": absolute_url,
            "title": title,
            "company": "XP Inc.",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(job.get("id")) if job.get("id") is not None else None,
            "posted_at": job.get("first_published"),
            "apply_url": absolute_url,
        }
        if departments:
            item["department"] = departments[0]
        if offices:
            item["locations"] = offices
        item.update(_salary_fields(job))

        yield {k: v for k, v in item.items() if v is not None and v != ""}
