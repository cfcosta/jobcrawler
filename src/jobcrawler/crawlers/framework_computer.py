
import html
import json
import re


SITE = "https://api.rippling.com/platform/api/ats/v1/board/framework/jobs"
EXTRA_HOSTS = []


def _text(value):
    if not value:
        return ""

    text = html.unescape(value)
    text = re.sub(
        r"<\s*(?:br|hr)\s*/?\s*>|<\s*/?\s*(?:address|article|aside|blockquote|div|footer|h[1-6]|header|li|main|ol|p|pre|section|table|td|th|tr|ul)\b[^>]*>",
        "\n",
        text,
        flags=re.I,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _workplace_type(location):
    match = re.match(r"^\s*(Remote|Hybrid|On-site|Onsite)\b", location or "", re.I)
    if not match:
        return None
    value = match.group(1).lower()
    if value == "remote":
        return "Remote"
    if value == "hybrid":
        return "Hybrid"
    return "On-site"


def crawl(ctx):
    listings = json.loads(ctx.fetch(SITE))

    for listing in listings:
        job_id = listing.get("uuid")
        if not job_id:
            continue

        detail = json.loads(ctx.fetch(SITE.rstrip("/") + "/" + job_id))
        description_parts = detail.get("description") or {}
        description = "\n\n".join(
            part for part in (
                _text(description_parts.get("company")),
                _text(description_parts.get("role")),
            )
            if part
        )

        work_locations = [
            value for value in (detail.get("workLocations") or [])
            if isinstance(value, str) and value.strip()
        ]
        listing_location = (listing.get("workLocation") or {}).get("label")
        location = work_locations[0] if work_locations else listing_location

        department_data = detail.get("department") or {}
        employment_data = detail.get("employmentType") or {}

        fields = {
            "url": detail.get("url") or listing.get("url"),
            "title": detail.get("name") or listing.get("name"),
            "company": "Framework",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": job_id,
        }

        if work_locations:
            fields["locations"] = work_locations

        department = department_data.get("name")
        if department:
            fields["department"] = department

        employment_type = employment_data.get("id")
        if employment_type:
            fields["employment_type"] = employment_type

        workplace_type = _workplace_type(location)
        if workplace_type:
            fields["workplace_type"] = workplace_type

        created_on = detail.get("createdOn")
        if created_on:
            fields["posted_at"] = created_on

        yield fields
