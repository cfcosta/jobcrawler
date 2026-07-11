"""Crawler for SolarWinds Greenhouse job board.

SolarWinds' public careers site (jobs.solarwinds.com) is a WordPress wrapper
that renders each role through /job-detail/?gh_jid=<id>; the underlying ATS is
Greenhouse under the board token ``solarwinds`` (board name "SolarWinds",
verified against boards-api.greenhouse.io). We fetch that board API directly.
"""

import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/solarwinds/jobs?content=true"


def _html_to_text(value):
    if not value:
        return None

    # Greenhouse board API returns the full content HTML-entity escaped (twice).
    text = html.unescape(value)
    text = html.unescape(text)

    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(
        r"(?i)</\s*(p|div|h[1-6]|li|ul|ol|section|article|tr)\s*>", "\n", text
    )
    text = re.sub(r"(?i)<\s*li[^>]*>", "- ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)

    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return text or None


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _job(raw):
    location = raw.get("location") or {}
    item = {
        "url": _clean(raw.get("absolute_url")),
        "title": _clean(raw.get("title")),
        "company": "SolarWinds",
        "location": _clean(
            location.get("name") if isinstance(location, dict) else location
        ),
        "description": _html_to_text(raw.get("content")),
        "source": SITE,
        "external_id": str(raw.get("id")) if raw.get("id") is not None else None,
        "apply_url": _clean(raw.get("absolute_url")),
        "posted_at": _clean(raw.get("first_published")),
    }

    departments = raw.get("departments")
    if departments:
        names = []
        for dep in departments:
            if isinstance(dep, dict) and dep.get("name"):
                names.append(dep["name"].strip())
            elif isinstance(dep, str) and dep.strip():
                names.append(dep.strip())
        if names:
            item["department"] = ", ".join(names)

    return {key: value for key, value in item.items() if value not in (None, "")}


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for raw in data.get("jobs", []):
        job = _job(raw)
        if job.get("url") and job.get("title") and job.get("description"):
            yield job
