from __future__ import annotations

import html
import json
import re

SITE = "https://token.bamboohr.com/careers/list"
EXTRA_HOSTS = []
COMPANY = "Token.io"


def _plain_text(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?i)<(?:br|/p|/div|/li|/h[1-6]|/tr|/section|/article)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _location(value):
    if not isinstance(value, dict):
        return None
    parts = []
    for key in ("city", "state", "postalCode", "addressCountry"):
        part = value.get(key)
        if part and part not in parts:
            parts.append(str(part))
    return ", ".join(parts) or None


def crawl(ctx):
    board = json.loads(ctx.fetch(SITE))

    for item in board.get("result") or []:
        external_id = str(item.get("id") or "").strip()
        if not external_id:
            continue

        detail_url = f"https://token.bamboohr.com/careers/{external_id}/detail"
        payload = json.loads(ctx.fetch(detail_url))
        opening = (payload.get("result") or {}).get("jobOpening") or {}
        if opening.get("jobOpeningStatus") not in (None, "Open"):
            continue

        title = opening.get("jobOpeningName") or item.get("jobOpeningName")
        description = _plain_text(opening.get("description"))
        location = _location(opening.get("location") or item.get("location"))
        url = opening.get("jobOpeningShareUrl") or (
            f"https://token.bamboohr.com/careers/{external_id}"
        )
        if not title or not description or not location:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": COMPANY,
            "location": location,
            "description": description,
            "external_id": external_id,
            "source": SITE,
        }

        department = opening.get("departmentLabel") or item.get("departmentLabel")
        if department:
            fields["department"] = department

        employment_type = (
            opening.get("employmentStatusLabel")
            or item.get("employmentStatusLabel")
        )
        if employment_type:
            fields["employment_type"] = employment_type

        location_type = opening.get("locationType") or item.get("locationType")
        workplace_types = {"1": "In Office", "2": "Remote", "3": "Flexible"}
        if str(location_type) in workplace_types:
            fields["workplace_type"] = workplace_types[str(location_type)]

        compensation = opening.get("compensation")
        if compensation:
            fields["salary"] = compensation

        posted_at = opening.get("datePosted")
        if posted_at:
            fields["posted_at"] = posted_at

        yield fields
