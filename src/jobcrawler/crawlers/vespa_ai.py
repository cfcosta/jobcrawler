
import html
import json
import re

SITE = "https://vespaai.bamboohr.com/careers/list"
EXTRA_HOSTS = []


def _text(value):
    value = html.unescape(value or "")
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)<li(?:\s[^>]*)?>", "\n- ", value)
    value = re.sub(
        r"(?i)</?(?:p|div|ul|ol|h[1-6]|section|article|table|tr)(?:\s[^>]*)?>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _location(record):
    location = record.get("location") or {}
    parts = [
        location.get("city"),
        location.get("state"),
        location.get("postalCode"),
        location.get("addressCountry"),
    ]
    return ", ".join(str(part) for part in parts if part)


def _workplace_type(item):
    if item.get("isRemote") is True:
        return "remote"
    if str(item.get("locationType")) == "0":
        return "on-site"
    return None


def crawl(ctx):
    listing = json.loads(ctx.fetch(SITE))
    for item in listing.get("result") or []:
        job_id = str(item["id"])
        detail_url = f"https://vespaai.bamboohr.com/careers/{job_id}/detail"
        payload = json.loads(ctx.fetch(detail_url))
        opening = (payload.get("result") or {}).get("jobOpening") or {}

        canonical_url = opening.get("jobOpeningShareUrl")
        title = opening.get("jobOpeningName") or item.get("jobOpeningName")
        description = _text(opening.get("description"))
        location = _location(opening) or _location(item)

        fields = {
            "url": canonical_url,
            "title": title,
            "company": "Vespa.ai",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": job_id,
            "apply_url": canonical_url,
        }

        department = opening.get("departmentLabel") or item.get("departmentLabel")
        employment_type = (
            opening.get("employmentStatusLabel")
            or item.get("employmentStatusLabel")
        )
        workplace_type = _workplace_type(item)

        if department:
            fields["department"] = department
        if employment_type:
            fields["employment_type"] = employment_type
        if workplace_type:
            fields["workplace_type"] = workplace_type

        yield fields
