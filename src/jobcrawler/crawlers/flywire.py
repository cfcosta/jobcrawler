"""Crawler for Flywire's SmartRecruiters job board (api.smartrecruiters.com postings)."""

import html
import json
import re
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

SITE = "https://api.smartrecruiters.com/v1/companies/Flywire1/postings?limit=100"
EXTRA_HOSTS = []


def _strip_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|section|tr)\s*>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _with_params(url, offset, limit):
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["offset"] = str(offset)
    query["limit"] = str(limit)
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def _location_text(loc):
    if not isinstance(loc, dict):
        return None
    return (
        loc.get("fullLocation")
        or ", ".join(
            [p for p in [loc.get("city"), loc.get("region"), loc.get("country")] if p]
        )
        or None
    )


def _description(detail):
    sections = (detail.get("jobAd") or {}).get("sections") or {}
    parts = []
    for key in (
        "companyDescription",
        "jobDescription",
        "qualifications",
        "additionalInformation",
    ):
        sec = sections.get(key)
        if isinstance(sec, dict):
            title = _strip_html(sec.get("title"))
            text = _strip_html(sec.get("text"))
            if title and text:
                parts.append(title + "\n" + text)
            elif text:
                parts.append(text)
    return "\n\n".join(parts).strip() if parts else None


def _make_job(detail, item):
    loc = detail.get("location") or item.get("location") or {}
    location = _location_text(loc)
    desc = _description(detail)
    title = detail.get("name") or item.get("name")
    if not (title and location and desc):
        return None

    job = {
        "url": item.get("ref") or detail.get("postingUrl") or detail.get("applyUrl"),
        "title": title,
        "company": ((detail.get("company") or {}).get("name")) or "Flywire",
        "location": location,
        "description": desc,
        "source": SITE,
        "locations": [location],
    }
    if detail.get("applyUrl"):
        job["apply_url"] = detail.get("applyUrl")
    if detail.get("refNumber"):
        job["external_id"] = detail.get("refNumber")
    elif detail.get("id"):
        job["external_id"] = detail.get("id")
    if detail.get("releasedDate"):
        job["posted_at"] = detail.get("releasedDate")
    employment = detail.get("typeOfEmployment") or item.get("typeOfEmployment")
    if isinstance(employment, dict) and employment.get("label"):
        job["employment_type"] = employment.get("label")
    function = detail.get("function") or item.get("function")
    if isinstance(function, dict) and function.get("label"):
        job["department"] = function.get("label")
    elif isinstance(function, str):
        job["department"] = function
    if isinstance(loc, dict):
        if loc.get("remote") is True:
            job["workplace_type"] = "Remote"
        elif loc.get("hybrid") is True:
            job["workplace_type"] = "Hybrid"
        elif loc.get("remote") is False or loc.get("hybrid") is False:
            job["workplace_type"] = "On-site"
    return job


def crawl(ctx):
    data = json.loads(ctx.fetch(_with_params(SITE, 0, 10)))
    for item in data.get("content") or []:
        ref = item.get("ref")
        if not ref:
            continue
        detail = json.loads(ctx.fetch(ref))
        job = _make_job(detail, item)
        if job:
            yield job
