
import html
import json
import re


SITE = "https://apply.workable.com/api/v1/widget/accounts/hotjar"
EXTRA_HOSTS = []


def _text(value):
    if not value:
        return None
    text = html.unescape(str(value))
    text = re.sub(
        r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)>",
        "",
        text,
    )
    text = re.sub(
        r"(?i)<(?:br|/p|/div|/li|/h[1-6]|/tr|/section|/article)\s*/?>",
        "\n",
        text,
    )
    text = re.sub(r"(?i)<li\b[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None


def _location_text(value):
    if isinstance(value, str):
        return value.strip() or None
    if not isinstance(value, dict):
        return None

    parts = []
    for key in ("city", "state", "region", "country"):
        part = value.get(key)
        if part:
            part = str(part).strip()
            if part and part not in parts:
                parts.append(part)
    return ", ".join(parts) or None


def _locations(summary, detail):
    raw = summary.get("locations")
    if not isinstance(raw, list):
        raw = detail.get("locations")

    values = []
    if isinstance(raw, list):
        for item in raw:
            value = _location_text(item)
            if value and value not in values:
                values.append(value)
    return values


def crawl(ctx):
    board = json.loads(ctx.fetch(SITE + "?details=true"))
    seen = set()

    for summary in board.get("jobs") or []:
        shortcode = summary.get("shortcode")
        if not shortcode:
            continue

        shortcode = str(shortcode)
        if shortcode in seen or not re.fullmatch(r"[A-Za-z0-9_-]+", shortcode):
            continue
        seen.add(shortcode)

        detail_url = (
            "https://apply.workable.com/api/v1/accounts/hotjar/jobs/"
            + shortcode
        )
        detail = json.loads(ctx.fetch(detail_url))

        state = detail.get("state")
        if state and str(state).lower() != "published":
            continue

        sections = []
        for key in ("description", "requirements", "benefits"):
            section = _text(detail.get(key))
            if section:
                sections.append(section)

        description = "\n\n".join(sections)
        title = detail.get("title") or summary.get("title")
        canonical_url = detail.get("url")
        location_values = _locations(summary, detail)

        primary_location = _location_text(detail.get("location"))
        if not primary_location:
            primary_location = _location_text(summary)
        if not primary_location and location_values:
            primary_location = location_values[0]
        if summary.get("telecommuting") and not primary_location:
            primary_location = "Remote"

        if not title or not canonical_url or not primary_location or not description:
            continue

        fields = {
            "url": canonical_url,
            "title": title,
            "company": "Hotjar",
            "location": primary_location,
            "description": description,
            "external_id": shortcode,
        }

        if location_values:
            fields["locations"] = location_values

        department = detail.get("department") or summary.get("department")
        if department:
            fields["department"] = department

        employment_type = (
            detail.get("employment_type")
            or summary.get("employment_type")
        )
        if employment_type:
            fields["employment_type"] = employment_type

        telecommuting = detail.get("telecommuting")
        detail_location = detail.get("location")
        if telecommuting is None and isinstance(detail_location, dict):
            telecommuting = detail_location.get("telecommuting")
        if telecommuting is None:
            telecommuting = summary.get("telecommuting")
        if telecommuting is True:
            fields["workplace_type"] = "remote"

        apply_url = detail.get("application_url")
        if apply_url:
            fields["apply_url"] = apply_url

        yield Job(**fields)
