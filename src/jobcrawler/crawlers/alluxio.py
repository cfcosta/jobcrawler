import html
import json
import re
from datetime import datetime, timezone


SITE = "https://api.lever.co/v0/postings/alluxio?mode=json"
EXTRA_HOSTS = []


def _plain(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/ul|/ol|/h[1-6]|/tr|/table)\s*/?\s*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    return text or None


def _description(posting):
    sections = []

    intro = posting.get("descriptionPlain") or _plain(posting.get("description"))
    if intro and intro.strip():
        sections.append(intro.strip())

    for item in posting.get("lists") or []:
        heading = _plain(item.get("text"))
        content = _plain(item.get("contentPlain") or item.get("content"))
        section = "\n".join(part for part in (heading, content) if part)
        if section:
            sections.append(section)

    additional = posting.get("additionalPlain") or _plain(posting.get("additional"))
    if additional and additional.strip():
        sections.append(additional.strip())

    return "\n\n".join(sections)


def _timestamp(epoch_ms):
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, timezone.utc).isoformat()


def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))

    for posting in postings:
        categories = posting.get("categories") or {}
        title = posting.get("text")
        url = posting.get("hostedUrl")
        location = categories.get("location")
        description = _description(posting)

        if not all((title, url, location, description)):
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Alluxio",
            "location": location,
            "description": description,
            "source": SITE,
        }

        optional = {
            "locations": categories.get("allLocations"),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("team"),
            "posted_at": _timestamp(posting.get("createdAt")),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        fields.update({key: value for key, value in optional.items() if value})
        yield fields
