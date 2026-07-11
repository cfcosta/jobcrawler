"""Crawler for Lattice's Greenhouse job board."""

import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/lattice/jobs?content=true"


def _clean_html(value):
    if not value:
        return None
    # Greenhouse content is HTML-entity escaped; unescape before and after tag removal.
    text = html.unescape(value)
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _metadata_value(job, name):
    for item in job.get("metadata") or []:
        if item.get("name") == name and item.get("value"):
            return item.get("value")
    return None


def crawl(ctx):
    """Yield one Job dict per open role from Lattice's Greenhouse boards API."""
    data = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))
    for job in data.get("jobs") or []:
        title = job.get("title")
        url = job.get("absolute_url")
        location = (job.get("location") or {}).get("name")
        description = _clean_html(job.get("content"))
        if not (title and url and location and description):
            continue

        depts = [d.get("name") for d in job.get("departments") or [] if d.get("name")]
        department = "; ".join(depts) if depts else None

        out = {
            "url": url,
            "title": title,
            "company": job.get("company_name") or "Lattice",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(job.get("id")) if job.get("id") is not None else None,
            "apply_url": url,
            "department": department,
            "employment_type": _metadata_value(job, "Employment Type"),
            "posted_at": job.get("first_published"),
        }

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

        yield {k: v for k, v in out.items() if v is not None and v != ""}
