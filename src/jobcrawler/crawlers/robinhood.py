
import json
import re
import html

SITE = "https://boards-api.greenhouse.io/v1/boards/robinhood/jobs?content=true"
EXTRA_HOSTS = []


def _text_from_html(value):
    if not value:
        return None

    text = html.unescape(value)

    # Preserve natural breaks before removing tags.
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", text)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|section|article|tr)\s*>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)

    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    return text or None


def _clean(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def crawl(ctx):
    raw = ctx.fetch(SITE + "&pay_transparency=true")
    data = json.loads(raw)
    for job in data.get("jobs") or []:
        title = _clean(job.get("title"))
        url = _clean(job.get("absolute_url"))
        location = _clean((job.get("location") or {}).get("name"))
        description = _text_from_html(job.get("content"))
        if not (title and url and location and description):
            continue

        item = {
            "url": url,
            "title": title,
            "company": "Robinhood",
            "location": location,
            "description": description,
            "source": SITE,
        }

        external_id = _clean(job.get("id"))
        if external_id:
            item["external_id"] = external_id

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
            item["salary_min"] = min(r["min_cents"] for r in bands) / 100
            item["salary_max"] = max(r["max_cents"] for r in bands) / 100
            item["salary_currency"] = next(iter(currencies))

        yield item
