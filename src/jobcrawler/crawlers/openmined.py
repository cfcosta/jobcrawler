
import html
import json
import re

SITE = "https://apply.workable.com/api/v1/widget/accounts/openmined"
EXTRA_HOSTS = []


def _strip_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|section|article|ul|ol)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _location_from_row(row):
    parts = []
    for key in ("city", "state", "country"):
        value = row.get(key)
        if value and value not in parts:
            parts.append(value)
    return ", ".join(parts) if parts else None


def _location_from_obj(loc):
    if isinstance(loc, str):
        return loc.strip() or None
    if not isinstance(loc, dict):
        return None
    parts = []
    for key in ("city", "region", "country"):
        value = loc.get(key)
        if value and value not in parts:
            parts.append(value)
    return ", ".join(parts) if parts else None


def _set_if_present(job, key, value):
    if value not in (None, ""):
        job[key] = value


def crawl(ctx):
    board = json.loads(ctx.fetch(SITE))
    company = board.get("name") or "OpenMined"

    by_shortcode = {}
    for row in board.get("jobs") or []:
        shortcode = row.get("shortcode")
        if not shortcode:
            continue

        entry = by_shortcode.setdefault(shortcode, {"base": row, "locations": []})
        for loc in row.get("locations") or []:
            loc_text = _location_from_obj(loc)
            if loc_text and loc_text not in entry["locations"]:
                entry["locations"].append(loc_text)

        row_loc = _location_from_row(row)
        if row_loc and row_loc not in entry["locations"]:
            entry["locations"].append(row_loc)

    for shortcode, entry in by_shortcode.items():
        base = entry["base"]
        detail_url = "https://apply.workable.com/api/v2/accounts/openmined/jobs/%s" % shortcode
        detail = json.loads(ctx.fetch(detail_url))

        body_parts = []
        for key in ("description", "requirements", "benefits"):
            text = _strip_html(detail.get(key))
            if text:
                body_parts.append(text)
        description = "\n\n".join(body_parts).strip()
        if not description:
            continue

        locations = entry["locations"]
        primary_location = locations[0] if locations else _location_from_row(base) or "Remote"

        job = {
            "url": base.get("url") or ("https://apply.workable.com/j/%s" % shortcode),
            "title": base.get("title") or detail.get("title"),
            "company": company,
            "location": primary_location,
            "description": description,
            "source": SITE,
        }

        if locations:
            job["locations"] = locations

        _set_if_present(job, "external_id", shortcode)
        _set_if_present(job, "apply_url", base.get("application_url") or detail.get("application_url"))
        _set_if_present(job, "department", base.get("department") or detail.get("department"))
        _set_if_present(job, "employment_type", base.get("employment_type") or detail.get("employment_type"))
        _set_if_present(job, "posted_at", base.get("published_on") or base.get("created_at"))

        if base.get("telecommuting") is True:
            job["workplace_type"] = "remote"
        elif base.get("telecommuting") is False:
            job["workplace_type"] = "on-site"

        yield job
