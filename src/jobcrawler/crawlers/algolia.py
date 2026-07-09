
import html
import json
import re
from datetime import datetime

SITE = "https://boards-api.greenhouse.io/v1/boards/algolia/jobs?content=true"
EXTRA_HOSTS = []


def _clean_html(value):
    if not value:
        return None

    text = html.unescape(value)
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|ul|ol)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _iso_datetime(value):
    if not value:
        return None
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        return datetime.fromisoformat(normalized).isoformat()
    except Exception:
        return value


def _first_name(items):
    if not items:
        return None
    first = items[0] or {}
    return first.get("name") or None


def crawl(ctx):
    response = ctx.fetch(SITE + "&pay_transparency=true")
    if hasattr(response, "json"):
        data = response.json()
    else:
        data = json.loads(response)

    for item in data.get("jobs", []):
        title = item.get("title")
        url = item.get("absolute_url")
        location = (item.get("location") or {}).get("name")
        description = _clean_html(item.get("content"))

        if not (title and url and location and description):
            continue

        job = {
            "url": url,
            "title": title,
            "company": "Algolia",
            "location": location,
            "description": description,
            "source": SITE,
        }

        external_id = item.get("id")
        if external_id is not None:
            job["external_id"] = str(external_id)

        apply_url = item.get("absolute_url")
        if apply_url:
            job["apply_url"] = apply_url

        department = _first_name(item.get("departments"))
        if department:
            job["department"] = department

        posted_at = _iso_datetime(item.get("first_published"))
        if posted_at:
            job["posted_at"] = posted_at

        expires_at = _iso_datetime(item.get("application_deadline"))
        if expires_at:
            job["expires_at"] = expires_at

        ranges = item.get("pay_input_ranges") or []
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
            job["salary_min"] = min(r["min_cents"] for r in bands) / 100
            job["salary_max"] = max(r["max_cents"] for r in bands) / 100
            job["salary_currency"] = next(iter(currencies))

        yield job
