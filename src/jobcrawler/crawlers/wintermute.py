import html
import json
import re
from datetime import datetime, timezone


SITE = 'https://api.lever.co/v0/postings/wintermute-trading?mode=json'
EXTRA_HOSTS = []


def _plain_text(value):
    if not value:
        return None

    # Preserve common block and list boundaries before removing markup.
    value = re.sub(r"(?i)<br\\s*/?>", "\\n", value)
    value = re.sub(r"(?i)<li(?:\\s[^>]*)?>", "\\n- ", value)
    value = re.sub(
        r"(?i)</?(?:div|p|h[1-6]|ul|ol|li|section|article|blockquote|tr)(?:\\s[^>]*)?>",
        "\\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\\n".join(lines) or None


def _posted_at(epoch_ms):
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp(
        epoch_ms / 1000, tz=timezone.utc
    ).isoformat().replace("+00:00", "Z")


def crawl(ctx):
    response = ctx.fetch(SITE)
    if isinstance(response, bytes):
        response = response.decode("utf-8")
    postings = json.loads(response)

    for posting in postings:
        categories = posting.get("categories") or {}
        sections = []

        intro = posting.get("descriptionPlain")
        if intro:
            sections.append(intro.strip())

        for item in posting.get("lists") or []:
            heading = _plain_text(item.get("text"))
            content = _plain_text(item.get("content"))
            section = "\\n".join(
                part for part in (heading, content) if part
            )
            if section:
                sections.append(section)

        additional = posting.get("additionalPlain")
        if not additional:
            additional = _plain_text(posting.get("additional"))
        if additional:
            sections.append(additional.strip())

        kwargs = {
            "url": posting["hostedUrl"],
            "title": posting["text"],
            "company": "Wintermute Trading",
            "location": categories.get("location"),
            "description": "\\n\\n".join(sections),
            "source": SITE,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("team"),
            "posted_at": _posted_at(posting.get("createdAt")),
        }

        all_locations = categories.get("allLocations")
        if all_locations:
            kwargs["locations"] = all_locations

        yield {
            key: value
            for key, value in kwargs.items()
            if value is not None and value != ""
        }
