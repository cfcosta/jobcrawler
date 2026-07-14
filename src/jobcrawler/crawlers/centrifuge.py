
import html
import json
import re
from datetime import datetime, timezone


SITE = "https://api.lever.co/v0/postings/centrifuge?mode=json"
EXTRA_HOSTS = []


def _plain_text(value):
    if not value:
        return None

    value = re.sub(
        r"(?i)<\s*br\s*/?\s*>|</?\s*(?:p|div|li|ul|ol|h[1-6]|section|tr|table)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        normalized = " ".join(line.split())
        if normalized:
            lines.append(normalized)
    return "\n".join(lines) or None


def _posted_at(epoch_ms):
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp(
        epoch_ms / 1000, tz=timezone.utc
    ).isoformat().replace("+00:00", "Z")


def _set_if_present(values, key, value):
    if value is not None and value != "":
        values[key] = value


def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))

    for posting in postings:
        categories = posting.get("categories") or {}
        body_parts = []

        description = _plain_text(posting.get("description"))
        if description:
            body_parts.append(description)

        for section in posting.get("lists") or []:
            heading = section.get("text")
            content = _plain_text(section.get("content"))
            if heading and heading.strip():
                body_parts.append(heading.strip())
            if content:
                body_parts.append(content)

        additional = _plain_text(posting.get("additional"))
        if additional:
            body_parts.append(additional)

        values = {
            "url": posting.get("hostedUrl"),
            "title": posting.get("text"),
            "company": "Centrifuge",
            "location": categories.get("location"),
            "source": SITE,
            "description": "\n\n".join(body_parts),
        }

        _set_if_present(
            values, "employment_type", categories.get("commitment")
        )
        _set_if_present(
            values, "workplace_type", posting.get("workplaceType")
        )
        _set_if_present(
            values,
            "department",
            categories.get("department") or categories.get("team"),
        )
        _set_if_present(
            values, "posted_at", _posted_at(posting.get("createdAt"))
        )
        _set_if_present(values, "external_id", posting.get("id"))
        _set_if_present(values, "apply_url", posting.get("applyUrl"))

        locations = categories.get("allLocations")
        if locations:
            values["locations"] = locations

        yield values
