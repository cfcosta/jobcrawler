import json
import re
from html import unescape
class Job(dict):
    pass


SITE = "https://kubermatic.bamboohr.com/careers/list"
EXTRA_HOSTS = []


def _text_from_html(value):
    value = value or ""
    value = re.sub(
        r"(?i)<\s*(?:br|p|div|li|ul|ol|h[1-6]|section|article|table|tr)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(
        r"(?i)<\s*/\s*(?:p|div|li|ul|ol|h[1-6]|section|article|table|tr)\s*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = unescape(value).replace("\xa0", " ")

    lines = []
    for line in value.splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _geo_location(record):
    geo = record.get("atsLocation") or {}
    parts = []
    for key in ("city", "state", "province", "country"):
        value = geo.get(key)
        if value and value not in parts:
            parts.append(value)
    return ", ".join(parts) if parts else None


def _location(opening, item, description):
    location = _geo_location(opening) or _geo_location(item)
    if location:
        return location

    match = re.search(
        r"(?im)^Location:\s*(.+)$",
        description,
    )
    if match:
        return match.group(1).strip()

    basic = opening.get("location") or item.get("location") or {}
    parts = []
    for key in ("city", "state", "addressCountry"):
        value = basic.get(key)
        if value and value not in parts:
            parts.append(value)
    return ", ".join(parts) if parts else None


def crawl(ctx):
    board = json.loads(ctx.fetch(SITE))

    for item in board.get("result") or []:
        job_id = item.get("id")
        if not job_id:
            continue

        detail_url = f"https://kubermatic.bamboohr.com/careers/{job_id}/detail"
        payload = json.loads(ctx.fetch(detail_url))
        opening = payload.get("result", {}).get("jobOpening", {})

        if opening.get("jobOpeningStatus") != "Open":
            continue

        description = _text_from_html(opening.get("description"))
        canonical_url = opening.get("jobOpeningShareUrl")
        title = opening.get("jobOpeningName")
        location = _location(opening, item, description)
        if not canonical_url or not title or not location or not description:
            continue

        fields = {
            "url": canonical_url,
            "title": title,
            "company": "Kubermatic",
            "source": SITE,
            "location": location,
            "description": description,
            "external_id": str(job_id),
            "apply_url": canonical_url,
        }

        optional = {
            "department": opening.get("departmentLabel"),
            "employment_type": opening.get("employmentStatusLabel"),
            "posted_at": opening.get("datePosted"),
        }
        fields.update({key: value for key, value in optional.items() if value})
        yield Job(**fields)
