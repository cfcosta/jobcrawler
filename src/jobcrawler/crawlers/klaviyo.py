from __future__ import annotations

import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/klaviyo/jobs?content=true"
EXTRA_HOSTS = []


def _text(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(?:p|div|li|ul|ol|h[1-6]|section|article)\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def crawl(ctx):
    url = SITE + "&pay_transparency=true"
    response = ctx.fetch(url)
    raw = response.text if hasattr(response, "text") else response
    data = json.loads(raw) if isinstance(raw, str) else raw

    for item in data.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        description = _text(item.get("content"))
        if not location or not description:
            continue

        job = {
            "url": item.get("absolute_url"),
            "title": item.get("title"),
            "company": item.get("company_name") or "Klaviyo",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(item["id"]),
            "posted_at": item.get("first_published"),
        }

        offices = item.get("offices") or []
        locations = [office.get("name") for office in offices if office.get("name")]
        if locations:
            job["locations"] = locations

        departments = item.get("departments") or []
        department_names = [entry.get("name") for entry in departments if entry.get("name")]
        if department_names:
            job["department"] = ", ".join(department_names)

        deadline = item.get("application_deadline")
        if deadline:
            job["expires_at"] = deadline

        ranges = item.get("pay_input_ranges") or []
        currencies = {entry.get("currency_type") for entry in ranges if entry.get("currency_type")}
        if len(ranges) == 1 and len(currencies) == 1:
            pay = ranges[0]
            if pay.get("min_cents") is not None:
                job["salary_min"] = pay["min_cents"] / 100
            if pay.get("max_cents") is not None:
                job["salary_max"] = pay["max_cents"] / 100
            job["salary_currency"] = next(iter(currencies))

        yield job
