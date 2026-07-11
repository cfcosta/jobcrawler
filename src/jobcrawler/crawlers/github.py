"""Crawler for GitHub's careers board (Jibe/iCIMS jobs API)."""

import json, html, re

SITE = "https://www.github.careers/api/jobs?limit=100"
COMPANY = "GitHub"
EXTRA_HOSTS = []

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_tags(html_text: str) -> str:
    """Unescape HTML entities then strip tags, normalizing whitespace."""
    if not html_text:
        return ""
    text = html.unescape(html_text)
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def crawl(ctx):
    """Yield one Job dict per open role from GitHub's Jibe jobs API."""
    data = json.loads(ctx.fetch(SITE))
    for entry in data.get("jobs", []):
        job = entry.get("data") or {}

        title = job.get("title")
        description = _strip_tags(job.get("description", ""))
        slug = job.get("slug") or job.get("req_id")
        if not (title and description and slug):
            continue

        location = job.get("location_name") or job.get("full_location")

        out = {
            "url": f"https://www.github.careers/careers-home/jobs/{slug}?lang=en-us",
            "title": title,
            "company": COMPANY,
            "location": location,
            "description": description,
            "source": SITE,
        }

        req_id = job.get("req_id") or slug
        if req_id:
            out["external_id"] = str(req_id)

        apply_url = job.get("apply_url")
        if apply_url:
            out["apply_url"] = apply_url

        cats = job.get("categories") or []
        department = "; ".join(c.get("name") for c in cats if c.get("name"))
        if department:
            out["department"] = department

        employment_type = job.get("employment_type")
        if employment_type:
            out["employment_type"] = employment_type

        posted_at = job.get("posted_date")
        if posted_at:
            out["posted_at"] = posted_at

        yield out
