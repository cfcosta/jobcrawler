"""Crawler for Atlassian's careers board (first-party listings JSON, iCIMS-backed)."""

import html
import json
import re
from datetime import datetime

SITE = "https://www.atlassian.com/endpoint/careers/listings"
EXTRA_HOSTS = []
COMPANY = "Atlassian"
DETAIL_BASE = "https://www.atlassian.com/company/careers/details/"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_tags(html_text):
    """Unescape HTML entities then strip tags, normalizing whitespace."""
    if not html_text:
        return ""
    text = html.unescape(html_text)
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def _clean(value):
    if not value:
        return ""
    return _WS_RE.sub(" ", html.unescape(str(value))).strip()


def _parse_posted(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d %I:%M %p").isoformat()
    except (ValueError, TypeError):
        return None


def crawl(ctx):
    """Yield one Job dict per open Atlassian role from the listings JSON array."""
    data = json.loads(ctx.fetch(SITE))
    if not isinstance(data, list):
        return

    seen = set()
    for job in data:
        if not isinstance(job, dict):
            continue

        job_id = job.get("id")
        if job_id is None or job_id in seen:
            continue
        seen.add(job_id)

        title = _clean(job.get("title"))
        url = DETAIL_BASE + str(job_id)

        sections = [
            job.get("overview"),
            job.get("responsibilities"),
            job.get("qualifications"),
        ]
        description = _strip_tags(" ".join(s for s in sections if s))

        if not (title and description and url):
            continue

        locations = []
        for loc in job.get("locations") or []:
            cleaned = _clean(loc)
            if cleaned and cleaned not in locations:
                locations.append(cleaned)

        out = {
            "url": url,
            "title": title,
            "company": COMPANY,
            "description": description,
            "source": SITE,
            "external_id": str(job_id),
        }

        if locations:
            out["location"] = locations[0]
            if len(locations) > 1:
                out["locations"] = locations

        portal = job.get("portalJobPost") or {}
        apply_url = job.get("applyUrl") or portal.get("portalUrl")
        if apply_url:
            out["apply_url"] = apply_url

        department = _clean(job.get("category"))
        if department:
            out["department"] = department

        employment_type = _clean(job.get("type"))
        if employment_type:
            out["employment_type"] = employment_type

        posted_at = _parse_posted(portal.get("updatedDate"))
        if posted_at:
            out["posted_at"] = posted_at

        yield out
