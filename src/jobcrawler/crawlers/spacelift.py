
import html
import json
import re

SITE = "https://careers.spacelift.io/jobs.json"
EXTRA_HOSTS = []


def _plain_text(value):
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(?:p|div|li|ul|ol|h[1-6]|section|article)>", "\n", value)
    value = re.sub(r"(?s)<[^>]*>", "", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def _location(place):
    address = (place or {}).get("address") or {}
    parts = []
    for key in ("addressLocality", "addressRegion", "addressCountry"):
        value = address.get(key)
        if value and str(value) not in parts:
            parts.append(str(value))
    return ", ".join(parts) if parts else None


def crawl(ctx):
    response = ctx.fetch(SITE)
    if hasattr(response, "json"):
        feed = response.json()
    else:
        body = response.text if hasattr(response, "text") else response
        feed = json.loads(body)

    if feed.get("title") != "Spacelift":
        return

    for item in feed.get("items", []):
        posting = item.get("_jobposting") or {}
        organization = posting.get("hiringOrganization") or {}
        description_html = item.get("content_html") or posting.get("description")
        title = item.get("title") or posting.get("title")
        url = item.get("url")

        if not title or not url or not description_html:
            continue

        locations = []
        for place in posting.get("jobLocation") or []:
            value = _location(place)
            if value and value not in locations:
                locations.append(value)

        identifier = posting.get("identifier") or {}
        external_id = identifier.get("value")
        if external_id is None:
            external_id = item.get("id")

        job = {
            "source": SITE,
            "url": url,
            "title": title,
            "company": organization.get("name") or "Spacelift",
            "description": _plain_text(description_html),
        }

        if locations:
            job["location"] = locations[0]
            job["locations"] = locations
        if posting.get("datePosted") or item.get("date_published"):
            job["posted_at"] = posting.get("datePosted") or item.get("date_published")
        if external_id is not None:
            job["external_id"] = str(external_id)

        yield job
