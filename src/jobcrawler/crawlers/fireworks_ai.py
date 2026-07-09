import html
import json
import re

SITE = 'https://boards-api.greenhouse.io/v1/boards/fireworksai/jobs?content=true'
EXTRA_HOSTS = []


def _html_to_text(value):
    text = html.unescape(value or "")
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|section)\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*(p|div|li|ul|ol|h[1-6]|section)[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def crawl(ctx):
    payload = ctx.fetch(SITE + "&pay_transparency=true")
    data = json.loads(payload)
    for job in data.get("jobs", []):
        title = (job.get("title") or "").strip()
        url = (job.get("absolute_url") or "").strip()
        location = ((job.get("location") or {}).get("name") or "").strip()
        description = _html_to_text(job.get("content"))

        if not (title and url and location and description):
            continue

        out = {
            "url": url,
            "title": title,
            "company": "Fireworks AI",
            "location": location,
            "description": description,
            "source": SITE,
        }

        external_id = job.get("id") or job.get("internal_job_id")
        if external_id is not None:
            out["external_id"] = str(external_id)

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
