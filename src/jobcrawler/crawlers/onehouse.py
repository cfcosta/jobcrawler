
import json
import re
from datetime import datetime, timezone


SITE = "https://api.lever.co/v0/postings/Onehouse?mode=json"
EXTRA_HOSTS = []


_ENTITIES = {
    "amp": "&",
    "lt": "<",
    "gt": ">",
    "quot": '"',
    "apos": "'",
    "nbsp": " ",
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


def _entity(match):
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


def _text(value):
    if not value:
        return None
    value = re.sub(
        r"(?i)</?(?:address|article|blockquote|br|dd|div|dl|dt|h[1-6]|li|ol|p|pre|section|table|tbody|td|th|tr|ul)[^>]*>",
        "\n",
        str(value),
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"&(#(?:[xX][0-9a-fA-F]+|[0-9]+)|[A-Za-z]+);", _entity, value)
    lines = []
    for line in value.splitlines():
        normalized = " ".join(line.split())
        if normalized:
            lines.append(normalized)
    text = "\n".join(lines).strip()
    return text or None


def _full_description(posting):
    parts = []
    intro = _text(posting.get("description"))
    if intro:
        parts.append(intro)

    for section in posting.get("lists") or []:
        heading = _text(section.get("text"))
        content = _text(section.get("content"))
        if heading:
            parts.append(heading)
        if content:
            parts.append(content)

    additional = _text(posting.get("additional"))
    if additional:
        parts.append(additional)
    return "\n\n".join(parts)


def _posted_at(epoch_ms):
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp(
        epoch_ms / 1000, tz=timezone.utc
    ).isoformat().replace("+00:00", "Z")


def crawl(ctx):
    response = ctx.fetch(SITE)
    postings = json.loads(response)

    for posting in postings:
        categories = posting.get("categories") or {}
        salary_range = posting.get("salaryRange") or {}
        fields = {
            "url": posting.get("hostedUrl"),
            "title": posting.get("text"),
            "company": "Onehouse",
            "location": categories.get("location"),
            "description": _full_description(posting),
            "source": SITE,
        }
        optional = {
            "locations": categories.get("allLocations"),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("team"),
            "posted_at": _posted_at(posting.get("createdAt")),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "salary": posting.get("salaryDescriptionPlain"),
            "salary_min": salary_range.get("min"),
            "salary_max": salary_range.get("max"),
            "salary_currency": salary_range.get("currency"),
            "salary_period": salary_range.get("interval"),
        }
        for key, value in optional.items():
            if value is not None and value != "" and value != []:
                fields[key] = value

        yield fields
