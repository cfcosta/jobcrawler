"""Crawler for Tigera's Greenhouse job board.

Tigera builds Calico, a Kubernetes-native network security and observability
platform. Its careers page embeds a Greenhouse board (token ``tigera``); this
reads the Greenhouse boards API directly.
"""

import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/tigera/jobs?content=true"
EXTRA_HOSTS = []


def _strip_html(value):
    if not value:
        return None
    # Greenhouse content is entity-escaped, sometimes more than once.
    text = value
    for _ in range(3):
        unescaped = html.unescape(text)
        if unescaped == text:
            break
        text = unescaped
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr|section|article)\s*>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*", "\n", text)
    return text.strip() or None


def _salary_fields(job):
    ranges = job.get("pay_input_ranges") or []
    bands = [
        r
        for r in ranges
        if isinstance(r, dict) and r.get("min_cents") and r.get("max_cents")
    ]
    currencies = {str(r.get("currency_type") or "").strip().upper() for r in bands}
    # Single-currency boards only: mixed-currency ranges (one per region)
    # cannot collapse into one honest band, so leave those unstated.
    if bands and len(currencies) == 1 and "" not in currencies:
        return {
            "salary_min": min(r["min_cents"] for r in bands) / 100,
            "salary_max": max(r["max_cents"] for r in bands) / 100,
            "salary_currency": next(iter(currencies)),
        }
    return {}


def crawl(ctx):
    raw = ctx.fetch(SITE + "&pay_transparency=true")
    data = json.loads(raw)
    for job in data.get("jobs") or []:
        title = (job.get("title") or "").strip()
        url = job.get("absolute_url")
        location = (job.get("location") or {}).get("name")
        description = _strip_html(job.get("content"))

        if not (title and url and description):
            continue

        item = {
            "url": url,
            "title": title,
            "company": "Tigera",
            "location": location,
            "description": description,
            "source": SITE,
        }

        if job.get("id") is not None:
            item["external_id"] = str(job.get("id"))
            item["apply_url"] = url
        if job.get("first_published"):
            item["posted_at"] = job.get("first_published")
        if job.get("updated_at"):
            item["updated_at"] = job.get("updated_at")
        if job.get("application_deadline"):
            item["expires_at"] = job.get("application_deadline")

        departments = [
            d.get("name") for d in (job.get("departments") or []) if d.get("name")
        ]
        if departments:
            item["department"] = departments[0]

        offices = [o.get("name") for o in (job.get("offices") or []) if o.get("name")]
        if offices:
            item["locations"] = offices

        item.update(_salary_fields(job))

        yield {k: v for k, v in item.items() if v is not None and v != ""}
