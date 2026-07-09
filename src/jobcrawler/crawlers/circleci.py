
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/circleci/jobs?content=true"
EXTRA_HOSTS = []

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_NL_RE = re.compile(r"\n{3,}")


def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|h[1-6]|li|ul|ol|section|article|tr)\s*>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = _NL_RE.sub("\n\n", text).strip()
    return text or None


def _names(items):
    if not isinstance(items, list):
        return None
    vals = []
    for item in items:
        if isinstance(item, dict) and item.get("name"):
            vals.append(str(item["name"]).strip())
    vals = [v for v in vals if v]
    return ", ".join(vals) if vals else None


def _job(**kwargs):
    return {k: v for k, v in kwargs.items() if v is not None and v != ""}


def crawl(ctx):
    raw = ctx.fetch(SITE + "&pay_transparency=true")
    data = json.loads(raw)
    for item in data.get("jobs", []):
        location = None
        if isinstance(item.get("location"), dict):
            location = item["location"].get("name")
        department = _names(item.get("departments"))
        out = _job(
            url=item.get("absolute_url"),
            title=item.get("title"),
            company=item.get("company_name") or "CircleCI",
            location=location,
            description=_clean_html(item.get("content")),
            source=SITE,
            department=department,
            posted_at=item.get("first_published"),
            expires_at=item.get("application_deadline"),
            external_id=str(item.get("id")) if item.get("id") is not None else None,
            apply_url=item.get("absolute_url"),
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
