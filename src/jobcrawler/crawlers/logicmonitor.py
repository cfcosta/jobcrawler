"""Crawler for LogicMonitor Greenhouse job board."""

import json
import html
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/logicmonitor/jobs?content=true"

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_RE = re.compile(r"\n\s*\n+")


def _clean_html(value):
    if not value:
        return None
    # Greenhouse `content=true` returns full posting HTML escaped as entities.
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|section|tr|ul|ol)\s*>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = _SPACE_RE.sub(" ", text)
    text = _BLANK_RE.sub("\n\n", text)
    return text.strip() or None


def _set_if_value(job, key, value):
    if value is not None and value != "":
        job[key] = value


def crawl(ctx):
    raw = ctx.fetch(SITE)
    data = json.loads(raw)
    for item in data.get("jobs", []):
        url = item.get("absolute_url")
        title = item.get("title")
        description = _clean_html(item.get("content"))
        if not url or not title or not description:
            continue

        location = item.get("location") or {}
        location_name = location.get("name") if isinstance(location, dict) else location

        depts = [
            d.get("name")
            for d in item.get("departments", [])
            if isinstance(d, dict) and d.get("name")
        ]
        department = "; ".join(depts) if depts else None

        job = {
            "url": url,
            "title": title,
            "company": item.get("company_name") or "LogicMonitor",
            "location": location_name,
            "description": description,
            "source": SITE,
        }

        _set_if_value(job, "department", department)
        _set_if_value(job, "posted_at", item.get("first_published"))
        _set_if_value(job, "expires_at", item.get("application_deadline"))
        _set_if_value(job, "external_id", item.get("requisition_id") or item.get("id"))
        _set_if_value(job, "apply_url", url)

        yield job
