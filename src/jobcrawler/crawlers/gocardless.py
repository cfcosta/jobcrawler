
import json
import re
from html import unescape

SITE = "https://boards-api.greenhouse.io/v1/boards/gocardless/jobs?content=true"
EXTRA_HOSTS = []


def _clean_html(value):
    if not value:
        return None

    text = value
    for _ in range(4):
        new_text = unescape(text)
        if new_text == text:
            break
        text = new_text

    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|section|tr)\s*>", "\n", text)
    text = re.sub(r"(?is)<\s*(script|style)[^>]*>.*?</\s*\1\s*>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)

    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _names(items):
    names = []
    for item in items or []:
        if isinstance(item, dict):
            name = item.get("name")
            if name:
                names.append(name)
    return names


def crawl(ctx):
    raw = ctx.fetch(SITE)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    data = json.loads(raw)

    for item in data.get("jobs") or []:
        title = (item.get("title") or "").strip()
        location = ((item.get("location") or {}).get("name") or "").strip()
        description = _clean_html(item.get("content"))
        if not title or not location or not description:
            continue

        departments = _names(item.get("departments"))
        offices = _names(item.get("offices"))

        job = {
            "url": item.get("absolute_url") or SITE,
            "title": title,
            "company": item.get("company_name") or "GoCardless",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(item.get("id")),
        }

        if offices:
            job["locations"] = offices
        if departments:
            job["department"] = departments[0]
        if item.get("first_published"):
            job["posted_at"] = item["first_published"]
        if item.get("application_deadline"):
            job["expires_at"] = item["application_deadline"]
        if item.get("absolute_url"):
            job["apply_url"] = item["absolute_url"]

        ranges = item.get("pay_input_ranges") or []
        currencies = set()
        for pay_range in ranges:
            currency = pay_range.get("currency_type")
            if currency:
                currencies.add(currency)

        if ranges and len(currencies) == 1:
            mins = []
            maxs = []
            for pay_range in ranges:
                if pay_range.get("min_cents") is not None:
                    mins.append(pay_range.get("min_cents"))
                if pay_range.get("max_cents") is not None:
                    maxs.append(pay_range.get("max_cents"))
            if mins:
                job["salary_min"] = min(mins) / 100
            if maxs:
                job["salary_max"] = max(maxs) / 100
            job["salary_currency"] = list(currencies)[0]

        yield job
