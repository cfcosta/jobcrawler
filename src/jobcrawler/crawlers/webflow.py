"""Crawler for Webflow's Greenhouse job board."""

import json, html, re

SITE = "https://boards-api.greenhouse.io/v1/boards/webflow/jobs?content=true"
COMPANY = "Webflow"
EXTRA_HOSTS = []

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_tags(html_text: str) -> str:
    """Unescape HTML entities then strip tags, normalizing whitespace."""
    if not html_text:
        return ""
    text = html.unescape(html_text)
    text = _TAG_RE.sub("", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def crawl(ctx):
    """Yield one Job dict per open role from the Greenhouse boards API."""
    raw = ctx.fetch(SITE + "&pay_transparency=true")
    data = json.loads(raw)
    for job in data.get("jobs", []):
        url = job.get("absolute_url")
        title = job.get("title")
        description = _strip_tags(job.get("content", ""))
        if not (url and title and description):
            continue

        loc = job.get("location") or {}
        location = loc.get("name")

        out = {
            "url": url,
            "title": title,
            "company": job.get("company_name") or COMPANY,
            "location": location,
            "description": description,
            "source": SITE,
        }

        external_id = job.get("id")
        if external_id:
            out["external_id"] = str(external_id)

        out["apply_url"] = url

        depts = [d.get("name") for d in job.get("departments", []) if d.get("name")]
        if depts:
            out["department"] = "; ".join(depts)

        posted_at = job.get("first_published")
        if posted_at:
            out["posted_at"] = posted_at

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
