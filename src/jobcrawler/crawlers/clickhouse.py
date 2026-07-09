
SITE = 'https://boards-api.greenhouse.io/v1/boards/clickhouse/jobs?content=true'
EXTRA_HOSTS = []

import json
import html
import re


def _clean(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _html_to_text(value):
    if not value:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|section|article|li|ul|ol|h[1-6])\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li\b[^>]*>", "\n- ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip() or None


def _join_names(items):
    names = []
    for item in items or []:
        if isinstance(item, dict):
            name = _clean(item.get("name"))
            if name:
                names.append(name)
    return ", ".join(names) if names else None


def _job(**kwargs):
    return {k: v for k, v in kwargs.items() if v is not None and v != ""}


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))
    for item in data.get("jobs", []):
        title = _clean(item.get("title"))
        url = _clean(item.get("absolute_url"))
        description = _html_to_text(item.get("content"))
        location_data = item.get("location") or {}
        location = _clean(location_data.get("name"))
        if not (title and url and description and location):
            continue

        out = _job(
            url=url,
            title=title,
            company="ClickHouse",
            location=location,
            description=description,
            source=SITE,
            department=_join_names(item.get("departments")),
            posted_at=_clean(item.get("first_published")),
            expires_at=_clean(item.get("application_deadline")),
            external_id=_clean(item.get("id")),
        )

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
            out["salary_min"] = min(r["min_cents"] for r in bands) / 100
            out["salary_max"] = max(r["max_cents"] for r in bands) / 100
            out["salary_currency"] = next(iter(currencies))

        yield out
