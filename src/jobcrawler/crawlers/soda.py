
import html
import json
import re

SITE = "https://apply.workable.com/api/v1/widget/accounts/soda-data-nv"
EXTRA_HOSTS = []


def _text(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(?:p|div|li|ul|ol|h[1-6])\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    lines = []
    for line in value.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines) or None


def _location(value):
    if not isinstance(value, dict):
        return None
    parts = []
    for key in ("city", "region", "country"):
        part = value.get(key)
        if part and part not in parts:
            parts.append(part)
    return ", ".join(parts) or None


def crawl(ctx):
    board = json.loads(ctx.fetch(SITE + "?details=true"))

    for listing in board.get("jobs", []):
        shortcode = listing.get("shortcode")
        description = _text(listing.get("description"))
        if not shortcode or not listing.get("title") or not description:
            continue

        locations = []
        for raw_location in listing.get("locations") or []:
            display = _location(raw_location)
            if display and display not in locations:
                locations.append(display)

        primary = _location({
            "city": listing.get("city"),
            "region": listing.get("state"),
            "country": listing.get("country"),
        })
        if not primary and locations:
            primary = locations[0]
        if not primary and listing.get("telecommuting") is True:
            primary = "Remote"
        if not primary:
            continue

        job = {
            "url": listing.get("url") or "https://apply.workable.com/j/" + shortcode,
            "title": listing["title"],
            "company": board.get("name") or "Soda Data",
            "location": primary,
            "description": description,
            "source": "Workable",
            "external_id": shortcode,
        }

        if locations:
            job["locations"] = locations
        if listing.get("application_url"):
            job["apply_url"] = listing["application_url"]
        if listing.get("employment_type"):
            job["employment_type"] = listing["employment_type"]
        if listing.get("telecommuting") is True:
            job["workplace_type"] = "remote"
        if listing.get("published_on"):
            job["posted_at"] = listing["published_on"]
        if listing.get("department"):
            job["department"] = listing["department"]

        yield job
