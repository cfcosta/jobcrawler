"""Crawler for Recurly's Workable job board (apply.workable.com widget API)."""

import json
import re
from html import unescape

SITE = "https://apply.workable.com/api/v1/widget/accounts/recurly?details=true"
EXTRA_HOSTS = []


def _response_text(resp):
    if isinstance(resp, str):
        return resp
    if hasattr(resp, "text"):
        text = resp.text
        return text() if callable(text) else text
    return str(resp)


def _fetch_json(ctx, url):
    resp = ctx.fetch(url)
    if hasattr(resp, "json"):
        try:
            return resp.json()
        except Exception:
            pass
    return json.loads(_response_text(resp))


def _html_to_text(*chunks):
    html = "\n".join(str(chunk) for chunk in chunks if chunk)
    html = unescape(html)
    html = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", html)
    html = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr|ul|ol)\s*>", "\n", html)
    html = re.sub(r"(?i)<\s*li[^>]*>", "\n- ", html)
    html = re.sub(r"<[^>]+>", " ", html)
    text = unescape(html)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _format_location(loc):
    if not isinstance(loc, dict):
        value = str(loc).strip()
        return value or None
    parts = []
    for key in ("city", "region", "country"):
        value = loc.get(key)
        if value:
            parts.append(str(value).strip())
    return ", ".join([part for part in parts if part]) or None


def _department(value):
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if item) or None
    return value or None


def crawl(ctx):
    board = _fetch_json(ctx, SITE)
    company = board.get("name") or "Recurly"
    seen = set()

    for summary in board.get("jobs") or []:
        shortcode = summary.get("shortcode")
        if not shortcode or shortcode in seen:
            continue
        seen.add(shortcode)

        detail_url = (
            "https://apply.workable.com/api/v1/accounts/recurly/jobs/%s" % shortcode
        )
        detail = _fetch_json(ctx, detail_url)

        if detail.get("state") and detail.get("state") != "published":
            continue

        description = _html_to_text(
            detail.get("description"),
            detail.get("requirements"),
            detail.get("benefits"),
        )
        if not description:
            continue

        loc_items = detail.get("locations") or summary.get("locations") or []
        locations = []
        for loc in loc_items:
            formatted = _format_location(loc)
            if formatted and formatted not in locations:
                locations.append(formatted)

        primary_location = (
            _format_location(detail.get("location"))
            or _format_location(summary)
            or (locations[0] if locations else None)
        )
        if not primary_location:
            primary_location = (
                "Remote"
                if (detail.get("remote") or summary.get("telecommuting"))
                else "Not specified"
            )

        job = {
            "url": summary.get("url")
            or summary.get("shortlink")
            or ("https://apply.workable.com/j/%s" % shortcode),
            "title": detail.get("title") or summary.get("title"),
            "company": company,
            "location": primary_location,
            "description": description,
            "source": SITE,
            "external_id": shortcode,
        }

        if locations:
            job["locations"] = locations

        apply_url = summary.get("application_url")
        if apply_url:
            job["apply_url"] = apply_url

        department = _department(detail.get("department")) or summary.get("department")
        if department:
            job["department"] = department

        employment_type = summary.get("employment_type") or detail.get("type")
        if employment_type:
            job["employment_type"] = employment_type

        workplace_type = detail.get("workplace")
        if not workplace_type and (
            detail.get("remote") or summary.get("telecommuting")
        ):
            workplace_type = "remote"
        if workplace_type:
            job["workplace_type"] = workplace_type

        posted_at = (
            detail.get("published")
            or summary.get("published_on")
            or summary.get("created_at")
        )
        if posted_at:
            job["posted_at"] = posted_at

        yield job
