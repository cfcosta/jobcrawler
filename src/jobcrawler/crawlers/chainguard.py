
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/chainguard/jobs?content=true"
EXTRA_HOSTS = []


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|section|article|tr)\s*>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    return text or None


def _metadata_value(job, name):
    for item in job.get("metadata") or []:
        if item.get("name") == name and item.get("value") not in (None, ""):
            return item.get("value")
    return None


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))
    for job in data.get("jobs") or []:
        title = job.get("title")
        url = job.get("absolute_url") or job.get("url")
        location = (job.get("location") or {}).get("name")
        description = _clean_html(job.get("content"))

        if not (title and url and location and description):
            continue

        out = {
            "url": url,
            "title": title,
            "company": "Chainguard",
            "location": location,
            "description": description,
            "source": SITE,
        }

        if job.get("id") is not None:
            out["external_id"] = str(job.get("id"))

        department = _metadata_value(job, "Careers Page Department") or _metadata_value(job, "Department")
        if department:
            out["department"] = department

        ranges = job.get("pay_input_ranges") or []
        bands = [
            r for r in ranges
            if isinstance(r, dict) and r.get("min_cents") and r.get("max_cents")
        ]
        currencies = {
            str(r.get("currency_type") or "").strip().upper() for r in bands
        }
        # Single-currency boards only: mixed-currency ranges (one per region)
        # cannot collapse into one honest band, so leave those unstated.
        if bands and len(currencies) == 1 and "" not in currencies:
            out["salary_min"] = min(r["min_cents"] for r in bands) / 100
            out["salary_max"] = max(r["max_cents"] for r in bands) / 100
            out["salary_currency"] = next(iter(currencies))

        yield out
