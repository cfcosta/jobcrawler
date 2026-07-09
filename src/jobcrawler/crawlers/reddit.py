
import json
import re
import html

SITE = 'https://boards-api.greenhouse.io/v1/boards/reddit/jobs?content=true'
EXTRA_HOSTS = []


def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?is)<(script|style).*?>.*?</\\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|section|article|tr)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _first_name(items):
    if not isinstance(items, list):
        return None
    names = []
    for item in items:
        if isinstance(item, dict) and item.get("name"):
            names.append(str(item["name"]).strip())
    return ", ".join(n for n in names if n) or None


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))
    for job in data.get("jobs", []):
        title = (job.get("title") or "").strip()
        url = (job.get("absolute_url") or "").strip()
        location = ""
        if isinstance(job.get("location"), dict):
            location = (job["location"].get("name") or "").strip()
        description = _clean_html(job.get("content"))

        if not (title and url and location and description):
            continue

        out = {
            "url": url,
            "title": title,
            "company": (job.get("company_name") or "Reddit").strip(),
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(job.get("id")),
        }

        department = _first_name(job.get("departments"))
        if department:
            out["department"] = department
        if job.get("first_published"):
            out["posted_at"] = job["first_published"]
        if job.get("application_deadline"):
            out["expires_at"] = job["application_deadline"]

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
