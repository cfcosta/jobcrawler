"""Crawler for the Dynatrace careers board (Coveo-backed search proxy)."""

import json
import re
import html
from datetime import datetime, timezone

SITE = "https://www.dynatrace.com/api/coveo/search/"
EXTRA_HOSTS = []

COMPANY = "Dynatrace"

# The careers UI POSTs an empty query with a large page size; the index holds a
# few hundred roles at most, so one request enumerates the whole board.
_SEARCH_BODY = {"q": "", "numberOfResults": 1000, "wildcards": True}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_NL_RE = re.compile(r"\n\s*\n+")


def _strip_html(value):
    """Unescape HTML entities then strip tags, preserving paragraph breaks."""
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?is)<script\b.*?</script>|<style\b.*?</style>", " ", text)
    text = re.sub(r"(?is)<\s*(br|p|div|li|tr|h[1-6])\b[^>]*>", "\n", text)
    text = re.sub(r"(?is)<\s*/\s*(p|div|li|tr|h[1-6])\s*>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = _NL_RE.sub("\n\n", text)
    text = "\n".join(part.strip() for part in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [v for v in value if v]
    return [value]


def _first(value):
    items = _as_list(value)
    return items[0] if items else None


def _workplace_type(flex):
    value = _first(flex)
    if not value:
        return None
    lowered = str(value).lower()
    if "remote" in lowered:
        return "Remote"
    if "hybrid" in lowered:
        return "Hybrid"
    if "office" in lowered or "on-site" in lowered or "onsite" in lowered:
        return "On-site"
    return value


def _posted_at(raw):
    ts = raw.get("date") or raw.get("indexeddate")
    if not isinstance(ts, (int, float)):
        return None
    try:
        return (
            datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
    except (ValueError, OverflowError, OSError):
        return None


def crawl(ctx):
    """Yield one Job dict per open Dynatrace role from the Coveo search proxy."""
    data = json.loads(ctx.fetch(SITE, method="POST", json=_SEARCH_BODY))

    for result in data.get("results", []):
        raw = result.get("raw") or {}

        title = (result.get("title") or "").strip() or None
        url = result.get("clickUri") or result.get("uri") or raw.get("uri")
        description = _strip_html(raw.get("description"))
        if not (title and url and description):
            continue

        cities = _as_list(raw.get("office_locations"))
        country = _first(raw.get("country"))
        locations = []
        for city in cities:
            entry = f"{city}, {country}" if country else str(city)
            if entry not in locations:
                locations.append(entry)
        location = locations[0] if locations else (country or None)
        if not location:
            continue

        job = {
            "url": url,
            "title": title,
            "company": COMPANY,
            "location": location,
            "description": description,
            "source": SITE,
        }

        if len(locations) > 1:
            job["locations"] = locations

        job_id = raw.get("id") or raw.get("job_id")
        if job_id:
            job["external_id"] = str(job_id)

        apply_url = raw.get("apply_url")
        if apply_url:
            job["apply_url"] = apply_url

        employment_type = _first(raw.get("employment_type"))
        if employment_type:
            job["employment_type"] = employment_type

        workplace_type = _workplace_type(raw.get("flex_option"))
        if workplace_type:
            job["workplace_type"] = workplace_type

        department = "; ".join(_as_list(raw.get("team")))
        if department:
            job["department"] = department

        posted_at = _posted_at(raw)
        if posted_at:
            job["posted_at"] = posted_at

        yield job
