import json
import re
import html

SITE = "https://boards-api.greenhouse.io/v1/boards/digitalocean98/jobs?content=true"
EXTRA_HOSTS = []

COMPANY = "DigitalOcean"


def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _location(job):
    loc = job.get("location") or {}
    if isinstance(loc, dict):
        return loc.get("name") or None
    if isinstance(loc, str):
        return loc or None
    return None


def crawl(ctx):
    raw = ctx.fetch(SITE + "&pay_transparency=true")
    data = json.loads(raw)
    for job in data.get("jobs", []):
        title = job.get("title")
        url = job.get("absolute_url")
        location = _location(job)
        description = _clean_html(job.get("content"))
        if not (title and url and location and description):
            continue

        item = {
            "url": url,
            "title": title,
            "company": COMPANY,
            "location": location,
            "description": description,
            "source": SITE,
        }

        external_id = job.get("id")
        if external_id is not None:
            item["external_id"] = str(external_id)

        if url:
            item["apply_url"] = url

        depts = [d.get("name") for d in job.get("departments", []) if d.get("name")]
        if depts:
            item["department"] = "; ".join(depts)

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
            item["salary_min"] = min(r["min_cents"] for r in bands) / 100
            item["salary_max"] = max(r["max_cents"] for r in bands) / 100
            item["salary_currency"] = next(iter(currencies))

        yield item
