
import json
import re
from html import unescape


SITE = "https://www.ua.la/sumate"
EXTRA_HOSTS = []


def clean_text(value):
    if not isinstance(value, str) or not value.strip():
        return None

    text = re.sub(
        r"<\s*(?:br|p|div|li|ul|ol|h[1-6]|section|article|tr|table|blockquote)\b[^>]*>",
        "\n",
        value,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"</\s*(?:p|div|li|ul|ol|h[1-6]|section|article|tr|table|blockquote)\s*>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text).replace("\xa0", " ")

    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        if line and (not lines or line != lines[-1]):
            lines.append(line)
    return "\n".join(lines) or None


def find_positions(value):
    if isinstance(value, dict):
        positions = value.get("positions")
        if isinstance(positions, list):
            return positions
        for child in value.values():
            result = find_positions(child)
            if result is not None:
                return result
    elif isinstance(value, list):
        for child in value:
            result = find_positions(child)
            if result is not None:
                return result
    return None


def position_location(position):
    values = []
    for key in ("country", "site"):
        value = position.get(key)
        if isinstance(value, str):
            value = value.strip()
            if value and value not in values:
                values.append(value)
    return ", ".join(values) if values else None


def optional_text(value):
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def crawl(ctx):
    page = ctx.fetch(SITE)
    match = re.search(
        r'<script[^>]*\bid=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script\s*>',
        page,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        raise ValueError("Next.js job data was not found")

    payload = json.loads(match.group(1))
    positions = find_positions(
        payload.get("props", {}).get("pageProps", {}).get("page", {})
    )
    if positions is None:
        raise ValueError("Positions array was not found")

    for position in positions:
        external_id = optional_text(position.get("id"))
        title = optional_text(position.get("title"))
        location = position_location(position)

        sections = []
        for key in ("description", "responsibilities", "requirements", "benefits"):
            section = clean_text(position.get(key))
            if section:
                sections.append(section)
        description = "\n\n".join(sections)

        if not external_id or not title or not location or not description:
            continue

        yield {
            "url": SITE + "/" + external_id,
            "title": title,
            "company": "Ualá",
            "location": location,
            "description": description,
            "source": SITE,
            "department": optional_text(position.get("department")),
            "external_id": external_id,
            "apply_url": optional_text(position.get("applyUrl")),
        }
