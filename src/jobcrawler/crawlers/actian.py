"""Crawler for Actian's Lever job board (api.lever.co v0 postings API, token `actian`)."""

import json
import re
import html
from datetime import datetime, timezone

SITE = "https://api.lever.co/v0/postings/actian?mode=json"
EXTRA_HOSTS = []
COMPANY = "Actian"


def _clean_html(value):
    if not value:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n", text)
    text = re.sub(r"(?i)</li\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip() or None


def _append(parts, value):
    text = _clean_html(value)
    if text and text not in parts:
        parts.append(text)


def _full_description(posting):
    parts = []
    _append(parts, posting.get("descriptionPlain") or posting.get("description"))
    for item in posting.get("lists") or []:
        heading = item.get("text")
        content = item.get("content")
        block_parts = []
        if heading:
            block_parts.append(_clean_html(heading))
        if content:
            block_parts.append(_clean_html(content))
        block = "\n".join(p for p in block_parts if p)
        _append(parts, block)
    _append(parts, posting.get("additionalPlain") or posting.get("additional"))
    return "\n\n".join(parts).strip()


def _iso_from_epoch_ms(value):
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).isoformat()
    except Exception:
        return None


def _salary_text(salary):
    if not isinstance(salary, dict):
        return None
    raw = salary.get("range")
    if raw:
        return str(raw).strip() or None
    minimum = salary.get("min")
    maximum = salary.get("max")
    currency = salary.get("currency")
    interval = salary.get("interval")
    if minimum is not None and maximum is not None and currency:
        suffix = (" " + str(interval)) if interval else ""
        return f"{currency} {minimum} - {maximum}{suffix}"
    return None


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for posting in data:
        categories = posting.get("categories") or {}
        title = (posting.get("text") or "").strip()
        url = (posting.get("hostedUrl") or "").strip()
        location = (categories.get("location") or "").strip()
        description = _full_description(posting)

        job = {
            "url": url,
            "title": title,
            "company": COMPANY,
            "location": location,
            "description": description,
            "source": SITE,
        }

        apply_url = (posting.get("applyUrl") or "").strip()
        if apply_url:
            job["apply_url"] = apply_url

        if posting.get("id"):
            job["external_id"] = str(posting["id"])

        posted_at = _iso_from_epoch_ms(posting.get("createdAt"))
        if posted_at:
            job["posted_at"] = posted_at

        workplace_type = posting.get("workplaceType")
        if workplace_type:
            job["workplace_type"] = str(workplace_type)

        commitment = categories.get("commitment")
        if commitment:
            job["employment_type"] = str(commitment)

        department = categories.get("department") or categories.get("team")
        if department:
            job["department"] = str(department)

        all_locations = categories.get("allLocations")
        if isinstance(all_locations, list):
            locations = [str(loc).strip() for loc in all_locations if str(loc).strip()]
            if locations:
                job["locations"] = locations

        salary = posting.get("salaryRange")
        if isinstance(salary, dict):
            printed = _salary_text(salary)
            if printed:
                job["salary"] = printed
            if salary.get("min") is not None:
                job["salary_min"] = salary.get("min")
            if salary.get("max") is not None:
                job["salary_max"] = salary.get("max")
            if salary.get("currency"):
                job["salary_currency"] = str(salary.get("currency"))
            if salary.get("interval"):
                job["salary_period"] = str(salary.get("interval"))

        if url and title and location and description:
            yield job
