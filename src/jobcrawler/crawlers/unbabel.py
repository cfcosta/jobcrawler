
import html
import json
import re


SITE = "https://apply.workable.com/api/v1/widget/accounts/unbabel"
EXTRA_HOSTS = []


def _text(value):
    if not value:
        return None
    value = html.unescape(str(value))
    value = re.sub(
        r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)>",
        "",
        value,
    )
    value = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|dd|div|dl|dt|"
        r"figcaption|figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|"
        r"section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    lines = []
    for line in value.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines) or None


def _json(response):
    if isinstance(response, (dict, list)):
        return response
    body = response.text if hasattr(response, "text") else str(response)
    return json.loads(body)


def _place(value):
    if isinstance(value, str):
        return value.strip() or None
    if not isinstance(value, dict):
        return None
    parts = []
    for key in ("city", "state", "country"):
        part = value.get(key)
        if part:
            part = str(part).strip()
            if part and part not in parts:
                parts.append(part)
    return ", ".join(parts) or None


def _primary_location(job):
    parts = []
    for key in ("city", "state", "country"):
        value = job.get(key)
        if value:
            value = str(value).strip()
            if value and value not in parts:
                parts.append(value)
    if parts:
        return ", ".join(parts)
    for value in job.get("locations") or []:
        place = _place(value)
        if place:
            return place
    if job.get("telecommuting") is True:
        return "Remote"
    return None


def crawl(ctx):
    board = _json(ctx.fetch(SITE + "?details=true"))
    company = board.get("name") or "Unbabel"

    for item in board.get("jobs") or []:
        if item.get("state") not in (None, "published"):
            continue

        shortcode = item.get("shortcode")
        if not shortcode or not item.get("title"):
            continue

        detail_url = (
            "https://apply.workable.com/api/v1/accounts/unbabel/jobs/"
            + str(shortcode)
        )
        detail = _json(ctx.fetch(detail_url))

        sections = []
        for key in ("description", "requirements", "benefits"):
            section = _text(detail.get(key))
            if section:
                sections.append(section)
        description = "\n\n".join(sections)
        if not description:
            continue

        canonical_url = (
            "https://apply.workable.com/unbabel/j/" + str(shortcode) + "/"
        )
        apply_url = (
            detail.get("application_url")
            or detail.get("url")
            or item.get("application_url")
            or item.get("url")
        )

        location = _primary_location(detail) or _primary_location(item)
        location_values = detail.get("locations") or item.get("locations") or []
        locations = []
        for value in location_values:
            place = _place(value)
            if place and place not in locations:
                locations.append(place)

        fields = {
            "url": canonical_url,
            "title": detail.get("title") or item.get("title"),
            "company": company,
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(shortcode),
            "department": detail.get("department") or item.get("department"),
            "employment_type": (
                detail.get("employment_type") or item.get("employment_type")
            ),
            "workplace_type": (
                "remote"
                if (
                    detail.get("telecommuting") is True
                    or item.get("telecommuting") is True
                )
                else None
            ),
            "locations": locations or None,
            "apply_url": apply_url,
            "posted_at": detail.get("published_on") or detail.get("created_at"),
            "expires_at": detail.get("expires_at"),
        }
        yield Job(
            **{
                key: value
                for key, value in fields.items()
                if value is not None
            }
        )
