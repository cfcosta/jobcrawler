
import json
import re
from html import unescape

SITE = "https://boards-api.greenhouse.io/v1/boards/verygoodventures/jobs?content=true"
EXTRA_HOSTS = []


def _html_to_text(value):
    if not value:
        return None

    text = unescape(value)

    # Preserve readable spacing around common block boundaries before tags are removed.
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|tr|td|th|section|blockquote)\s*>", "\n", text)
    text = re.sub(r"(?is)<\s*(script|style)[^>]*>.*?</\s*\1\s*>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)

    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return text or None


def _names(items):
    names = []
    for item in items or []:
        if isinstance(item, dict):
            name = item.get("name")
            if name:
                names.append(name)
    return names


def _salary_fields(job):
    ranges = job.get("pay_input_ranges") or []
    if not ranges:
        return {}

    currencies = {r.get("currency_type") for r in ranges if r.get("currency_type")}
    if len(currencies) != 1:
        return {}

    mins = [r.get("min_cents") for r in ranges if isinstance(r.get("min_cents"), (int, float))]
    maxs = [r.get("max_cents") for r in ranges if isinstance(r.get("max_cents"), (int, float))]

    result = {"salary_currency": currencies.pop()}
    if mins:
        result["salary_min"] = min(mins) / 100
    if maxs:
        result["salary_max"] = max(maxs) / 100

    periods = [r.get("pay_period") for r in ranges if r.get("pay_period")]
    if periods:
        result["salary_period"] = periods[0]

    return result


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for item in data.get("jobs") or []:
        title = item.get("title")
        url = item.get("absolute_url")
        location = (item.get("location") or {}).get("name")
        description = _html_to_text(item.get("content"))

        if not (title and url and location and description):
            continue

        job = {
            "url": url,
            "title": title,
            "company": item.get("company_name") or "Very Good Ventures",
            "location": location,
            "description": description,
            "external_id": str(item.get("id")),
            "source": SITE,
        }

        departments = _names(item.get("departments"))
        if departments:
            job["department"] = departments[0]

        offices = _names(item.get("offices"))
        if offices:
            job["locations"] = offices

        if item.get("first_published"):
            job["posted_at"] = item.get("first_published")

        job.update(_salary_fields(item))
        yield job
