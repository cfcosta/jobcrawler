
import json
import re


SITE = 'https://www.comeet.co/careers-api/2.0/company/27.006/positions?token=7262AE41572157215721C984056E4C405623BE'
EXTRA_HOSTS = []


_NAMED_ENTITIES = {
    "amp": "&",
    "apos": "'",
    "gt": ">",
    "lt": "<",
    "nbsp": " ",
    "quot": '"',
    "ndash": "-",
    "mdash": "-",
    "bull": "-",
    "middot": "-",
    "hellip": "...",
    "rsquo": "'",
    "lsquo": "'",
    "rdquo": '"',
    "ldquo": '"',
}


def _decode_entity(match):
    value = match.group(1)
    if value.startswith("#x") or value.startswith("#X"):
        try:
            return chr(int(value[2:], 16))
        except (ValueError, OverflowError):
            return match.group(0)
    if value.startswith("#"):
        try:
            return chr(int(value[1:]))
        except (ValueError, OverflowError):
            return match.group(0)
    return _NAMED_ENTITIES.get(value, match.group(0))


def _html_to_text(value):
    if not value:
        return None

    text = re.sub(
        r"(?is)<\s*(?:br|/p|/div|/section|/article|/li|/ul|/ol|"
        r"h[1-6]|/h[1-6])\b[^>]*>",
        "\n",
        value,
    )
    text = re.sub(r"(?is)<\s*li\b[^>]*>", "- ", text)
    text = re.sub(r"(?is)<[^>]+>", "", text)
    text = re.sub(r"&([A-Za-z]+|#[0-9]+|#[xX][0-9A-Fa-f]+);", _decode_entity, text)
    text = text.replace("\r", "\n")

    lines = []
    for raw_line in text.split("\n"):
        line = " ".join(raw_line.split())
        if line:
            lines.append(line)
    return "\n".join(lines) or None


def _description(position):
    sections = []
    details = position.get("details") or []

    for section in sorted(details, key=lambda item: item.get("order") or 0):
        body = _html_to_text(section.get("value"))
        if not body:
            continue
        name = section.get("name")
        if name:
            sections.append(name + "\n" + body)
        else:
            sections.append(body)

    return "\n\n".join(sections) or None


def crawl(ctx):
    separator = "&" if "?" in SITE else "?"
    positions = json.loads(ctx.fetch(SITE + separator + "details=true"))

    for position in positions:
        if position.get("is_internal"):
            continue

        url = (
            position.get("url_comeet_hosted_page")
            or position.get("url_active_page")
            or position.get("url_recruit_hosted_page")
        )
        location_data = position.get("location") or {}

        values = {
            "url": url,
            "source": SITE,
            "title": position.get("name"),
            "company": position.get("company_name") or "Akeyless",
            "location": location_data.get("name"),
            "description": _description(position),
            "department": position.get("department"),
            "employment_type": position.get("employment_type"),
            "workplace_type": position.get("workplace_type"),
            "external_id": position.get("uid"),
            "apply_url": url,
        }
        yield {key: value for key, value in values.items() if value is not None}
