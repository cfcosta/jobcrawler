
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/yugabyte/jobs?content=true"
EXTRA_HOSTS = []


def _clean_html(value):
    if not value:
        return None

    # Greenhouse content is entity-escaped HTML; unescape repeatedly for double-escaped boards.
    text = value
    for _ in range(3):
        new_text = html.unescape(text)
        if new_text == text:
            break
        text = new_text

    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</?(p|div|li|ul|ol|h[1-6]|tr|table|section|article)[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n+ *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None


def _names(items):
    out = []
    for item in items or []:
        if isinstance(item, dict):
            name = item.get("name")
        else:
            name = item
        if name:
            name = str(name).strip()
            if name:
                out.append(name)
    return out


def _with_pay_transparency(url):
    return url + ("&" if "?" in url else "?") + "pay_transparency=true"


def _fetch_text(ctx, url):
    try:
        return ctx.fetch(url)
    except TypeError:
        return ctx.fetch(url, "GET", "")


def crawl(ctx):
    data = json.loads(_fetch_text(ctx, _with_pay_transparency(SITE)))
    for job in data.get("jobs", []):
        title = (job.get("title") or "").strip()
        location = ((job.get("location") or {}).get("name") or "").strip()
        description = _clean_html(job.get("content"))
        url = job.get("absolute_url") or SITE
        if not (title and location and description and url):
            continue

        departments = _names(job.get("departments"))
        offices = _names(job.get("offices"))

        item = {
            "url": url,
            "title": title,
            "company": (job.get("company_name") or "YugabyteDB").strip(),
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(job.get("id")),
        }

        if offices:
            item["locations"] = offices
        if departments:
            item["department"] = ", ".join(departments)
        if job.get("first_published"):
            item["posted_at"] = job.get("first_published")
        if job.get("application_deadline"):
            item["expires_at"] = job.get("application_deadline")
        if job.get("absolute_url"):
            item["apply_url"] = job.get("absolute_url")

        ranges = job.get("pay_input_ranges") or []
        currencies = {r.get("currency_type") for r in ranges if r.get("currency_type")}
        if ranges and len(currencies) == 1:
            mins = [r.get("min_cents") for r in ranges if isinstance(r.get("min_cents"), (int, float))]
            maxs = [r.get("max_cents") for r in ranges if isinstance(r.get("max_cents"), (int, float))]
            if mins:
                item["salary_min"] = min(mins) / 100.0
            if maxs:
                item["salary_max"] = max(maxs) / 100.0
            item["salary_currency"] = next(iter(currencies))
            period = next((r.get("unit") or r.get("period") for r in ranges if r.get("unit") or r.get("period")), None)
            if period:
                item["salary_period"] = period

        yield item
