"""Crawler for Docusign careers (Jibe front-end over iCIMS, JSON board)."""

import json, html, re

SITE = "https://careers.docusign.com/api/jobs"
COMPANY = "Docusign"
EXTRA_HOSTS = []

_PAGE_LIMIT = 100
_MAX_PAGES = 60  # safety cap; ~313 roles / 100 per page

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean(text: str) -> str:
    """Unescape HTML entities then strip tags, normalizing whitespace."""
    if not text:
        return ""
    text = html.unescape(text)
    text = _TAG_RE.sub("", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def crawl(ctx):
    """Yield one Job dict per open role from the Docusign/Jibe jobs API."""
    seen = set()
    page = 1
    while page <= _MAX_PAGES:
        raw = ctx.fetch(f"{SITE}?page={page}&limit={_PAGE_LIMIT}")
        data = json.loads(raw)
        jobs = data.get("jobs") or []
        if not jobs:
            break

        for entry in jobs:
            d = entry.get("data") or {}
            slug = d.get("slug") or d.get("req_id")
            if not slug or slug in seen:
                continue
            seen.add(slug)

            title = d.get("title")
            description = _clean(d.get("description") or "")
            url = f"https://careers.docusign.com/careers-home/jobs/{slug}"
            location = d.get("full_location") or d.get("location_name")
            if not (title and description and url):
                continue

            out = {
                "url": url,
                "title": title,
                "company": COMPANY,
                "location": location,
                "description": description,
                "source": SITE,
            }

            req_id = d.get("req_id") or slug
            if req_id:
                out["external_id"] = str(req_id)

            apply_url = d.get("apply_url")
            if apply_url:
                out["apply_url"] = apply_url

            employment_type = d.get("employment_type")
            if employment_type:
                out["employment_type"] = employment_type

            tags2 = d.get("tags2") or []
            if tags2 and tags2[0]:
                out["workplace_type"] = tags2[0]

            cats = d.get("categories") or []
            depts = [
                c.get("name") for c in cats if isinstance(c, dict) and c.get("name")
            ]
            if depts:
                out["department"] = "; ".join(depts)

            posted_at = d.get("posted_date")
            if posted_at:
                out["posted_at"] = posted_at

            full_location = d.get("full_location")
            if full_location and ";" in full_location:
                locations = []
                for part in full_location.split(";"):
                    part = part.strip()
                    if part and part not in locations:
                        locations.append(part)
                if len(locations) > 1:
                    out["locations"] = locations

            yield out

        total = data.get("totalCount") or data.get("count") or 0
        if len(seen) >= total or len(jobs) < _PAGE_LIMIT:
            break
        page += 1
