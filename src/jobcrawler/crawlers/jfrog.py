import json
import re


SITE = 'https://boards-api.greenhouse.io/v1/boards/jfrog/jobs?content=true'
EXTRA_HOSTS = []


_NAMED_ENTITIES = {
    "amp": "&",
    "lt": "<",
    "gt": ">",
    "quot": '"',
    "apos": "'",
    "nbsp": " ",
    "ndash": "-",
    "mdash": "-",
    "hellip": "...",
    "rsquo": "'",
    "lsquo": "'",
    "ldquo": '"',
    "rdquo": '"',
    "bull": "*",
    "middot": "*",
    "trade": "(TM)",
    "reg": "(R)",
    "copy": "(C)",
}


def _decode_entity(match):
    value = match.group(1)
    if value.startswith("#x") or value.startswith("#X"):
        try:
            return chr(int(value[2:], 16))
        except ValueError:
            return " "
    if value.startswith("#"):
        try:
            return chr(int(value[1:]))
        except ValueError:
            return " "
    return _NAMED_ENTITIES.get(value, " ")


def _text(value):
    if not value:
        return None

    # Greenhouse escapes the entire HTML body, occasionally more than once.
    decoded = value
    for unused in range(3):
        updated = re.sub(r"&([A-Za-z]+|#[0-9]+|#[xX][0-9A-Fa-f]+);", _decode_entity, decoded)
        if updated == decoded:
            break
        decoded = updated

    decoded = re.sub(
        r"(?i)</?(?:p|div|section|article|h[1-6]|ul|ol|li|br|tr|table|blockquote|pre)[^>]*>",
        "\n",
        decoded,
    )
    decoded = re.sub(r"<[^>]+>", " ", decoded)

    lines = []
    for line in decoded.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines) or None


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for item in payload.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        locations = [
            office.get("name")
            for office in item.get("offices") or []
            if office.get("name")
        ]
        departments = [
            department.get("name")
            for department in item.get("departments") or []
            if department.get("name")
        ]
        url = item.get("absolute_url")

        yield {
            "url": url,
            "source": SITE,
            "title": item.get("title"),
            "company": item.get("company_name") or "JFrog",
            "location": location,
            "locations": locations or None,
            "description": _text(item.get("content")),
            "department": ", ".join(departments) or None,
            "posted_at": item.get("first_published"),
            "expires_at": item.get("application_deadline"),
            "external_id": str(item["id"]) if item.get("id") is not None else None,
            "apply_url": url,
        }
