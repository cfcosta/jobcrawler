"""
Crawler for Duolingo Greenhouse board.
Uses the Greenhouse boards API for full enumeration of all open roles.
"""

import json
import html
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/duolingo/jobs?content=true"

API_URL = "https://boards-api.greenhouse.io/v1/boards/duolingo/jobs?content=true"
COMPANY = "Duolingo"


def _strip_tags(text):
    """Remove HTML tags, unescape entities, and normalize whitespace."""
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"</(p|div|li|h[1-6]|br|tr)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<(p|div|li|h[1-6]|br|tr)[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    text = text.strip()
    return text


def crawl(ctx=None):
    """
    Yield one Job dict per open role on the Duolingo Greenhouse board.
    Uses the Greenhouse boards API which returns every open role in a single GET.
    The `content` field is HTML-entity-escaped; we unescape and strip tags
    to produce clean plain-text descriptions.
    """
    raw = ctx.fetch(API_URL + "&pay_transparency=true")
    data = json.loads(raw)

    for job in data.get("jobs", []):
        location_name = job.get("location", {}).get("name", "") or ""

        dept_names = [
            d.get("name", "") for d in job.get("departments", []) if d.get("name")
        ]
        department = ", ".join(dept_names) if dept_names else None

        division = None
        for m in job.get("metadata", []):
            if m.get("name") == "Division" and m.get("value"):
                division = m["value"]
        if not department and division:
            department = division

        description = _strip_tags(job.get("content", ""))
        posted_at = job.get("first_published")
        expires_at = job.get("application_deadline")
        external_id = str(job.get("id")) if job.get("id") else None
        apply_url = job.get("absolute_url", "") or None
        url = job.get("absolute_url", "") or None

        out = {
            "url": url,
            "title": job.get("title", ""),
            "company": COMPANY,
            "location": location_name or None,
            "description": description,
            "source": SITE,
            "department": department,
            "posted_at": posted_at,
            "expires_at": expires_at,
            "external_id": external_id,
            "apply_url": apply_url,
        }

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
