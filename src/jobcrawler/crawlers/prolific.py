
import html
import json
import re

SITE = 'https://boards-api.greenhouse.io/v1/boards/prolific/jobs?content=true'
EXTRA_HOSTS = []


def _description(value):
    text = html.unescape(value or "")
    text = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1\s*>", " ", text)
    text = re.sub(
        r"(?i)<\s*(br|hr|/p|/div|/li|/ul|/ol|/h[1-6]|/tr|/table|/section|/article)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?i)<\s*li\b[^>]*>", "\n- ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)

    lines = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if line and (not lines or line != lines[-1]):
            lines.append(line)
    return "\n".join(lines)


def _add_pay(job, ranges):
    valid = [item for item in (ranges or []) if isinstance(item, dict)]
    currencies = {
        item.get("currency_type")
        for item in valid
        if item.get("currency_type")
    }
    if not valid or len(currencies) != 1:
        return

    minimums = [
        item["min_cents"] / 100
        for item in valid
        if item.get("min_cents") is not None
    ]
    maximums = [
        item["max_cents"] / 100
        for item in valid
        if item.get("max_cents") is not None
    ]
    if minimums:
        job["salary_min"] = min(minimums)
    if maximums:
        job["salary_max"] = max(maximums)
    if minimums or maximums:
        job["salary_currency"] = next(iter(currencies))


def crawl(ctx):
    api_url = SITE + ("&" if "?" in SITE else "?") + "pay_transparency=true"
    data = json.loads(ctx.fetch(api_url))
    seen = set()

    for item in data.get("jobs", []):
        external_id = item.get("id")
        url = item.get("absolute_url")
        key = external_id if external_id is not None else url
        if not key or key in seen:
            continue
        seen.add(key)

        title = item.get("title")
        location = (item.get("location") or {}).get("name")
        description = _description(item.get("content"))
        if not title or not url or not location or not description:
            continue

        job = {
            "url": url,
            "title": title,
            "company": "Prolific",
            "location": location,
            "description": description,
            "source": SITE,
        }

        offices = [
            office.get("name")
            for office in (item.get("offices") or [])
            if isinstance(office, dict) and office.get("name")
        ]
        if offices:
            job["locations"] = offices

        departments = [
            department.get("name")
            for department in (item.get("departments") or [])
            if isinstance(department, dict) and department.get("name")
        ]
        if departments:
            job["department"] = ", ".join(departments)

        if external_id is not None:
            job["external_id"] = str(external_id)
        if item.get("first_published"):
            job["posted_at"] = item["first_published"]
        if item.get("application_deadline"):
            job["expires_at"] = item["application_deadline"]

        _add_pay(job, item.get("pay_input_ranges"))
        yield job
