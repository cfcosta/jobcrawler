
import html
import json
import re

SITE = 'https://boards-api.greenhouse.io/v1/boards/accrue/jobs?content=true'
EXTRA_HOSTS = []

def _html_to_text(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</?(p|div|section|article|h[1-6]|ul|ol|tr|table)[^>]*>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "\n- ", text)
    text = re.sub(r"(?i)</li>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\r", "\n", text)
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None

def _names(items):
    out = []
    for item in items or []:
        if isinstance(item, dict):
            name = item.get("name")
            if name:
                out.append(str(name))
    return out

def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for raw in data.get("jobs") or []:
        title = raw.get("title")
        loc = (raw.get("location") or {}).get("name")
        desc = _html_to_text(raw.get("content"))
        url = raw.get("absolute_url")
        if not (title and loc and desc and url):
            continue

        job = {
            "url": url,
            "title": title,
            "company": raw.get("company_name") or "Accrue",
            "location": loc,
            "description": desc,
            "source": SITE,
            "external_id": str(raw.get("id")),
            "apply_url": url,
        }

        departments = _names(raw.get("departments"))
        if departments:
            job["department"] = departments[0]

        offices = _names(raw.get("offices"))
        if offices:
            job["locations"] = offices

        if raw.get("first_published"):
            job["posted_at"] = raw.get("first_published")
        if raw.get("application_deadline"):
            job["expires_at"] = raw.get("application_deadline")

        ranges = raw.get("pay_input_ranges") or []
        currencies = set()
        for r in ranges:
            if r.get("currency_type"):
                currencies.add(r.get("currency_type"))
        if ranges and len(currencies) == 1:
            lows = []
            highs = []
            for r in ranges:
                if isinstance(r.get("min_cents"), (int, float)):
                    lows.append(r.get("min_cents"))
                if isinstance(r.get("max_cents"), (int, float)):
                    highs.append(r.get("max_cents"))
            if lows:
                job["salary_min"] = min(lows) / 100
            if highs:
                job["salary_max"] = max(highs) / 100
            job["salary_currency"] = list(currencies)[0]

        yield job
