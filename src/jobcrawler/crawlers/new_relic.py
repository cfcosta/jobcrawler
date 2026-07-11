"""Crawler for New Relic's Greenhouse job board."""

import json
import re
import html

SITE = "https://boards-api.greenhouse.io/v1/boards/newrelic/jobs?content=true&pay_transparency=true"
COMPANY = "New Relic"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")


def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(
        r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|tr|section|article)\s*>", "\n", text
    )
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
    """Yield one Job dict per open role from New Relic's Greenhouse board."""
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
            "title": title.strip(),
            "company": raw.get("company_name") or COMPANY,
            "location": location,
            "description": description,
            "source": SITE,
        }

        external_id = raw.get("id")
        if external_id is not None:
            job["external_id"] = str(external_id)

        job["apply_url"] = url

        department = _flatten_names(raw.get("departments"))
        if department:
            job["department"] = department

        posted_at = raw.get("first_published") or raw.get("updated_at")
        if posted_at:
            job["posted_at"] = posted_at

        ranges = raw.get("pay_input_ranges") or []
        bands = [
            r
            for r in ranges
            if isinstance(r, dict) and r.get("min_cents") and r.get("max_cents")
        ]
        currencies = {str(r.get("currency_type") or "").strip().upper() for r in bands}
        # Single-currency boards only: mixed-currency ranges (one per region)
        # cannot collapse into one honest band, so leave those unstated.
        if bands and len(currencies) == 1 and "" not in currencies:
            job["salary_min"] = min(r["min_cents"] for r in bands) / 100
            job["salary_max"] = max(r["max_cents"] for r in bands) / 100
            job["salary_currency"] = next(iter(currencies))

        yield job
