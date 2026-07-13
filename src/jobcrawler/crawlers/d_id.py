
import html
import json
import re


SITE = "https://apply.workable.com/api/v1/widget/accounts/d-id"
EXTRA_HOSTS = []


def _text(value):
    if value is None:
        return None
    text = html.unescape(str(value))
    text = re.sub(
        r"(?i)<br\s*/?>|</(?:p|div|li|ul|ol|section|article|h[1-6]|tr)>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None


def _location_name(value):
    if isinstance(value, str):
        return value.strip() or None
    if not isinstance(value, dict):
        return None

    for key in ("name", "location_str", "full_location"):
        name = value.get(key)
        if isinstance(name, str) and name.strip():
            return name.strip()

    parts = []
    for key in ("city", "region", "country"):
        part = value.get(key)
        if isinstance(part, str) and part.strip() and part.strip() not in parts:
            parts.append(part.strip())
    return ", ".join(parts) or None


def _pick(listed, detail, key):
    value = detail.get(key)
    return value if value is not None else listed.get(key)


def crawl(ctx):
    board = json.loads(ctx.fetch(SITE + "?details=true"))
    company = board.get("name") or "D-ID"

    seen = set()
    for listed in board.get("jobs") or []:
        shortcode = listed.get("shortcode")
        if not shortcode or shortcode in seen:
            continue
        seen.add(shortcode)

        detail_payload = json.loads(
            ctx.fetch(SITE + "/jobs/" + str(shortcode))
        )
        detail = detail_payload.get("job", detail_payload)
        if not isinstance(detail, dict):
            detail = {}

        sections = []
        for key in ("description", "requirements", "benefits"):
            section = _text(detail.get(key))
            if section and section not in sections:
                sections.append(section)
        description = "\n\n".join(sections)
        if not description:
            continue

        primary_location = _location_name(_pick(listed, detail, "location"))
        raw_locations = _pick(listed, detail, "locations")
        locations = []
        if isinstance(raw_locations, list):
            for raw_location in raw_locations:
                name = _location_name(raw_location)
                if name and name not in locations:
                    locations.append(name)
        if not locations and primary_location:
            locations = [primary_location]
        if not primary_location and locations:
            primary_location = locations[0]

        fields = {
            "url": (
                "https://apply.workable.com/d-id/j/"
                + str(shortcode)
                + "/"
            ),
            "title": _pick(listed, detail, "title"),
            "company": company,
            "location": primary_location,
            "description": description,
            "source": SITE,
        }

        external_id = _pick(listed, detail, "id")
        if external_id is not None:
            fields["external_id"] = str(external_id)

        optional = {
            "apply_url": _pick(listed, detail, "application_url"),
            "employment_type": _pick(
                listed, detail, "employment_type"
            ),
            "department": _pick(listed, detail, "department"),
            "posted_at": _pick(listed, detail, "created_at"),
        }
        for key, value in optional.items():
            if value is not None and value != "":
                fields[key] = value

        if locations:
            fields["locations"] = locations
        if _pick(listed, detail, "telecommuting") is True:
            fields["workplace_type"] = "remote"

        yield Job(**fields)
