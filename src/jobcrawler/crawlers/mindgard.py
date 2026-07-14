
import json
import re
from html import unescape


SITE = "https://apply.workable.com/api/v1/widget/accounts/mindgard"
EXTRA_HOSTS = []


def _plain_text(value):
    if not value:
        return None
    text = unescape(str(value))
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/ul|/ol|/h[1-6]|/section|/tr)\s*/?>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text).replace("\r", "")
    lines = []
    for line in text.split("\n"):
        clean = " ".join(line.split())
        if clean:
            lines.append(clean)
    return "\n".join(lines) or None


def _location_text(location):
    if isinstance(location, str):
        return location.strip() or None
    if not isinstance(location, dict):
        return None
    parts = [
        location.get("city"),
        location.get("state"),
        location.get("country"),
    ]
    text = ", ".join(str(part).strip() for part in parts if part)
    return text or None


def _locations(item):
    result = []
    for entry in item.get("locations") or []:
        text = _location_text(entry)
        if text and text not in result:
            result.append(text)
    return result


def _primary_location(item, locations):
    for key in ("location", "location_str"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    flat = _location_text({
        "city": item.get("city"),
        "state": item.get("state"),
        "country": item.get("country"),
    })
    if flat:
        return flat
    return locations[0] if locations else None


def _full_description(detail, summary):
    sections = []
    for key in ("description", "requirements", "benefits"):
        value = detail.get(key)
        if value is None:
            value = summary.get(key)
        text = _plain_text(value)
        if text and text not in sections:
            sections.append(text)
    return "\n\n".join(sections) or None


def _optional(value):
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def crawl(ctx):
    board = json.loads(ctx.fetch(SITE + "?details=true"))

    for item in board.get("jobs") or []:
        shortcode = _optional(item.get("shortcode"))
        if not shortcode:
            continue

        shortcode = str(shortcode)
        detail_url = (
            "https://apply.workable.com/api/v1/accounts/mindgard/jobs/"
            + shortcode
        )
        detail = json.loads(ctx.fetch(detail_url))

        locations = _locations(detail) or _locations(item)
        location = _primary_location(detail, locations)
        if location is None:
            location = _primary_location(item, locations)

        apply_url = _optional(
            detail.get("application_url")
            or detail.get("url")
            or item.get("application_url")
            or item.get("url")
        )

        telecommuting = detail.get("telecommuting")
        if telecommuting is None:
            telecommuting = item.get("telecommuting")

        yield Job(
            url="https://apply.workable.com/mindgard/j/" + shortcode,
            title=detail.get("title") or item.get("title"),
            company="Mindgard",
            location=location,
            locations=locations or None,
            description=_full_description(detail, item),
            employment_type=_optional(
                detail.get("employment_type") or item.get("employment_type")
            ),
            workplace_type="remote" if telecommuting is True else None,
            department=_optional(
                detail.get("department")
                or item.get("department")
                or detail.get("function")
                or item.get("function")
            ),
            external_id=shortcode,
            apply_url=apply_url,
        )
