import json
import re
from datetime import datetime, timezone



SITE = "https://api.lever.co/v0/postings/tinybird?mode=json"
EXTRA_HOSTS = []


_ENTITIES = {
    "amp": "&",
    "lt": "<",
    "gt": ">",
    "quot": '"',
    "apos": "'",
    "nbsp": " ",
    "ndash": "-",
    "mdash": "--",
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
        except ValueError:
            return match.group(0)
    if value.startswith("#"):
        try:
            return chr(int(value[1:]))
        except ValueError:
            return match.group(0)
    return _ENTITIES.get(value, match.group(0))


def _html_to_text(value):
    if not value:
        return None
    value = re.sub(
        r"(?i)<\s*(br|p|div|li|ul|ol|h[1-6]|section|article|blockquote|pre)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(
        r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|section|article|blockquote|pre)\s*>",
        "\n",
        value,
    )
    value = re.sub(r"(?s)<[^>]+>", "", value)
    value = re.sub(r"&(#(?:[xX][0-9a-fA-F]+|[0-9]+)|[A-Za-z]+);", _decode_entity, value)

    lines = []
    for line in value.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines) or None


def _full_description(posting):
    sections = []

    intro = posting.get("descriptionPlain")
    if not intro:
        intro = _html_to_text(posting.get("description"))
    if intro and intro.strip():
        sections.append(intro.strip())

    for block in posting.get("lists") or []:
        section = []
        heading = block.get("text")
        content = _html_to_text(block.get("content"))
        if heading and heading.strip():
            section.append(heading.strip())
        if content:
            section.append(content)
        if section:
            sections.append("\n".join(section))

    additional = posting.get("additionalPlain")
    if not additional:
        additional = _html_to_text(posting.get("additional"))
    if additional and additional.strip():
        sections.append(additional.strip())

    return "\n\n".join(sections)


def _posted_at(epoch_ms):
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp(
        epoch_ms / 1000, tz=timezone.utc
    ).isoformat().replace("+00:00", "Z")


def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))

    for posting in postings:
        categories = posting.get("categories") or {}
        locations = [
            value
            for value in (categories.get("allLocations") or [])
            if isinstance(value, str) and value.strip()
        ]

        fields = {
            "url": posting.get("hostedUrl"),
            "title": posting.get("text"),
            "company": "Tinybird",
            "location": categories.get("location"),
            "description": _full_description(posting),
            "source": SITE,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "workplace_type": posting.get("workplaceType"),
            "employment_type": categories.get("commitment"),
            "department": categories.get("department") or categories.get("team"),
            "posted_at": _posted_at(posting.get("createdAt")),
        }
        if locations:
            fields["locations"] = locations

        yield {
            key: value
            for key, value in fields.items()
            if value is not None
        }
