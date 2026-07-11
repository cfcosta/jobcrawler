
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/bitwarden/jobs?content=true"
EXTRA_HOSTS = []


def _strip_html(value):
    if not value:
        return None
    # Greenhouse content is HTML-entity-escaped HTML.
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|ul|ol|tr)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _api_url():
    if "pay_transparency=true" in SITE:
        return SITE
    return SITE + ("&" if "?" in SITE else "?") + "pay_transparency=true"


def crawl(ctx):
    data = json.loads(ctx.fetch(_api_url()))
    for item in data.get("jobs", []):
        title = (item.get("title") or "").strip()
        description = _strip_html(item.get("content"))
        location_obj = item.get("location") or {}
        location = location_obj.get("name") if isinstance(location_obj, dict) else location_obj

        job = {
            "url": item.get("absolute_url"),
            "title": title,
            "company": item.get("company_name") or "Bitwarden",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(item.get("id")),
        }

        if item.get("first_published"):
            job["posted_at"] = item.get("first_published")
        if item.get("application_deadline"):
            job["expires_at"] = item.get("application_deadline")
        if item.get("absolute_url"):
            job["apply_url"] = item.get("absolute_url")

        departments = [d.get("name") for d in item.get("departments", []) if isinstance(d, dict) and d.get("name")]
        if departments:
            job["department"] = departments[0]

        offices = [o.get("name") for o in item.get("offices", []) if isinstance(o, dict) and o.get("name")]
        if offices:
            job["locations"] = offices

        ranges = item.get("pay_input_ranges") or []
        currencies = {r.get("currency_type") for r in ranges if isinstance(r, dict) and r.get("currency_type")}
        if ranges and len(currencies) == 1:
            mins = [r.get("min_cents") for r in ranges if isinstance(r, dict) and r.get("min_cents") is not None]
            maxs = [r.get("max_cents") for r in ranges if isinstance(r, dict) and r.get("max_cents") is not None]
            if mins:
                job["salary_min"] = min(mins) / 100
            if maxs:
                job["salary_max"] = max(maxs) / 100
            job["salary_currency"] = next(iter(currencies))
            periods = [r.get("unit") or r.get("interval") for r in ranges if isinstance(r, dict) and (r.get("unit") or r.get("interval"))]
            if periods:
                job["salary_period"] = periods[0]

        if job["url"] and job["title"] and job["location"] and job["description"]:
            yield job
