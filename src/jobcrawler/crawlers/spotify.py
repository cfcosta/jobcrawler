import json, re, html
from datetime import datetime, timezone

SITE = "https://api.lever.co/v0/postings/spotify?mode=json"


def crawl(ctx):
    """Crawl Spotify's Lever board and yield Job objects."""
    resp = ctx.fetch(SITE)
    data = json.loads(resp)

    for posting in data:
        # Reconstruct full description from description + lists + additional
        parts = []

        # Main description (intro)
        desc_plain = posting.get("descriptionPlain", "") or ""
        if desc_plain:
            parts.append(desc_plain.strip())

        # Lists sections (What You'll Do, Who You Are, etc.)
        for lst in posting.get("lists", []):
            list_text = lst.get("text", "")
            list_content = lst.get("content", "")
            section = ""
            if list_text:
                section += list_text.strip() + "\n"
            if list_content:
                section += list_content.strip()
            if section.strip():
                parts.append(section.strip())

        # Additional (footer/equal opportunity)
        add_plain = posting.get("additionalPlain", "") or ""
        if add_plain:
            parts.append(add_plain.strip())

        full_description = "\n\n".join(parts)

        # Extract structured fields
        categories = posting.get("categories", {}) or {}

        # Location: prefer allLocations if multiple, else location
        all_locs = categories.get("allLocations", [])
        if all_locs and len(all_locs) > 0:
            location = ", ".join(all_locs)
        else:
            location = categories.get("location", "") or ""

        # Employment type from commitment
        employment_type = categories.get("commitment", "") or ""

        # Department
        department = categories.get("department", "") or ""
        team = categories.get("team", "") or ""

        # Workplace type
        workplace_type = posting.get("workplaceType", "") or ""

        # Posted at - epoch ms
        created_at = posting.get("createdAt")
        posted_at = None
        if created_at:
            posted_at = datetime.fromtimestamp(
                created_at / 1000, tz=timezone.utc
            ).isoformat()

        # IDs and URLs
        posting_id = posting.get("id", "")
        hosted_url = posting.get("hostedUrl", "")
        apply_url = posting.get("applyUrl", "")

        job = {
            "url": hosted_url,
            "title": posting.get("text", ""),
            "company": "Spotify",
            "location": location,
            "description": full_description,
            "source": SITE,
            "employment_type": employment_type,
            "workplace_type": workplace_type,
            "department": department,
            "posted_at": posted_at,
            "external_id": posting_id,
            "apply_url": apply_url,
        }

        yield job
