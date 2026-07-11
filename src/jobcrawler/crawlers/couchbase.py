
import json
import re
import html

SITE = "https://boards-api.greenhouse.io/v1/boards/couchbaseinc/jobs?content=true"
EXTRA_HOSTS = []

TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"[ \t\r\f\v]+")
BLANK_RE = re.compile(r"\n\s*\n+")

def _text_from_greenhouse_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|section|tr)\s*>", "\n", text)
    text = TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = SPACE_RE.sub(" ", text)
    text = BLANK_RE.sub("\n\n", text)
    return text.strip() or None

def _api_url():
    if "pay_transparency=" in SITE:
        return SITE
    sep = "&" if "?" in SITE else "?"
    return SITE + sep + "pay_transparency=true"

def _first_name(items):
    if isinstance(items, list) and items:
        first = items[0] or {}
        return first.get("name") or None
    return None

def _names(items):
    out = []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and item.get("name"):
                out.append(item["name"])
    return out

def _pay_fields(job):
    ranges = job.get("pay_input_ranges") or []
    currencies = {r.get("currency_type") for r in ranges if r.get("currency_type")}
    if len(currencies) != 1:
        return {}
    mins, maxs = [], []
    for r in ranges:
        if isinstance(r.get("min_cents"), int):
            mins.append(r["min_cents"] / 100)
        if isinstance(r.get("max_cents"), int):
            maxs.append(r["max_cents"] / 100)
    if not mins and not maxs:
        return {}
    fields = {"salary_currency": next(iter(currencies))}
    if mins:
        fields["salary_min"] = min(mins)
    if maxs:
        fields["salary_max"] = max(maxs)
    return fields

def crawl(ctx):
    data = json.loads(ctx.fetch(_api_url()))
    for job in data.get("jobs", []):
        location = (job.get("location") or {}).get("name")
        description = _text_from_greenhouse_html(job.get("content"))
        if not (job.get("absolute_url") and job.get("title") and location and description):
            continue

        item = {
            "url": job.get("absolute_url"),
            "title": job.get("title"),
            "company": job.get("company_name") or "Couchbase",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(job.get("id")) if job.get("id") is not None else None,
            "posted_at": job.get("first_published"),
            "department": _first_name(job.get("departments")),
            "apply_url": job.get("absolute_url"),
        }

        offices = _names(job.get("offices"))
        if offices:
            item["locations"] = offices

        item.update(_pay_fields(job))

        yield {k: v for k, v in item.items() if v is not None}
