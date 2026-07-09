
import json
import html
import re

SITE = 'https://boards-api.greenhouse.io/v1/boards/mongodb/jobs?content=true'
EXTRA_HOSTS = []


def _clean(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _html_to_text(value):
    if not value:
        return None

    text = html.unescape(value)

    # Preserve paragraph/list structure before removing the remaining markup.
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|section|article|blockquote|tr)\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li(?:\s+[^>]*)?>", "\n- ", text)
    text = re.sub(r"<[^>]+>", " ", text)

    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))
    for item in data.get("jobs", []):
        title = _clean(item.get("title"))
        url = _clean(item.get("absolute_url"))
        description = _html_to_text(item.get("content"))

        location = item.get("location") or {}
        if isinstance(location, dict):
            location_name = _clean(location.get("name"))
        else:
            location_name = _clean(location)

        if not (title and url and description and location_name):
            continue

        job = {
            "url": url,
            "title": title,
            "company": "MongoDB",
            "location": location_name,
            "description": description,
            "source": SITE,
            "external_id": _clean(item.get("id")),
        }

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

        yield {key: value for key, value in job.items() if value is not None}
