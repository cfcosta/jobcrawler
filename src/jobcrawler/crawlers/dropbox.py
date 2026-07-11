import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/dropbox/jobs?content=true"
EXTRA_HOSTS = []

COMPANY = "Dropbox"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|h[1-6]|li|ul|ol|section|tr)\s*>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    return text or None


def _first_name(items):
    if not isinstance(items, list):
        return None
    for item in items:
        if isinstance(item, dict):
            name = item.get("name")
            if name:
                return name
    return None


def _metadata_value(items, name):
    if not isinstance(items, list):
        return None
    target = name.lower()
    for item in items:
        if isinstance(item, dict) and str(item.get("name", "")).lower() == target:
            value = item.get("value")
            if value:
                return str(value)
    return None


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))
    for job in data.get("jobs", []):
        title = job.get("title")
        url = job.get("absolute_url")
        location = (job.get("location") or {}).get("name")
        description = _clean_html(job.get("content"))
        if not (title and url and location and description):
            continue

        out = {
            "url": url,
            "title": title.strip(),
            "company": job.get("company_name") or COMPANY,
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(job.get("id")),
            "apply_url": url,
        }

        posted_at = job.get("first_published")
        if posted_at:
            out["posted_at"] = posted_at

        department = _first_name(job.get("departments"))
        if department:
            out["department"] = department

        # Time Type ("Full Time") is the honest full/part-time signal;
        # the board's "Employment Type" only carries worker class ("Regular").
        employment_type = _metadata_value(job.get("metadata"), "Time Type")
        if employment_type:
            out["employment_type"] = employment_type

        ranges = job.get("pay_input_ranges") or []
        bands = [
            r
            for r in ranges
            if isinstance(r, dict) and r.get("min_cents") and r.get("max_cents")
        ]
        currencies = {str(r.get("currency_type") or "").strip().upper() for r in bands}
        # Single-currency boards only: mixed-currency ranges (one per region)
        # cannot collapse into one honest band, so leave those unstated.
        if bands and len(currencies) == 1 and "" not in currencies:
            out["salary_min"] = min(r["min_cents"] for r in bands) / 100
            out["salary_max"] = max(r["max_cents"] for r in bands) / 100
            out["salary_currency"] = next(iter(currencies))

        yield out
