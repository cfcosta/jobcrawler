
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/soloioinc/jobs?content=true"
EXTRA_HOSTS = []


def _strip_html(value):
    if value is None:
        return None
    # Greenhouse content can be entity-escaped more than once.
    text = value
    for _ in range(3):
        unescaped = html.unescape(text)
        if unescaped == text:
            break
        text = unescaped
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr|section|article)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*", "\n", text)
    return text.strip()


def _api_url():
    joiner = "&" if "?" in SITE else "?"
    return SITE + joiner + "pay_transparency=true"


def _salary_fields(job):
    ranges = job.get("pay_input_ranges") or []
    if not ranges:
        return {}

    currencies = {r.get("currency_type") for r in ranges if r.get("currency_type")}
    if len(currencies) != 1:
        return {}

    mins = [r.get("min_cents") for r in ranges if r.get("min_cents") is not None]
    maxs = [r.get("max_cents") for r in ranges if r.get("max_cents") is not None]
    out = {"salary_currency": next(iter(currencies))}
    if mins:
        out["salary_min"] = min(mins) / 100
    if maxs:
        out["salary_max"] = max(maxs) / 100

    # Greenhouse range objects sometimes carry interval/period labels on other boards.
    for key in ("pay_period", "interval", "period"):
        for r in ranges:
            if r.get(key):
                out["salary_period"] = r.get(key)
                break
        if "salary_period" in out:
            break
    return out


def crawl(ctx):
    raw = ctx.fetch(_api_url())
    data = json.loads(raw)
    for job in data.get("jobs", []):
        title = job.get("title")
        url = job.get("absolute_url")
        location_obj = job.get("location") or {}
        location = location_obj.get("name")
        description = _strip_html(job.get("content"))

        if not (title and url and location and description):
            continue

        item = {
            "url": url,
            "title": title,
            "company": "Solo.io",
            "location": location,
            "description": description,
            "source": SITE,
        }

        if job.get("id") is not None:
            item["external_id"] = str(job.get("id"))
        if job.get("first_published"):
            item["posted_at"] = job.get("first_published")
        if job.get("application_deadline"):
            item["expires_at"] = job.get("application_deadline")
        if job.get("absolute_url"):
            item["apply_url"] = job.get("absolute_url")

        departments = [d.get("name") for d in (job.get("departments") or []) if d.get("name")]
        if departments:
            item["department"] = departments[0]

        offices = [o.get("name") for o in (job.get("offices") or []) if o.get("name")]
        if offices:
            item["locations"] = offices

        item.update(_salary_fields(job))
        yield item
