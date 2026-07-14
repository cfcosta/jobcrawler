
import json
import re
from datetime import datetime, timezone
from html import unescape


SITE = "https://api.lever.co/v0/postings/scality?mode=json"
EXTRA_HOSTS = []


def _plain(value):
    if not value:
        return None

    text = re.sub(
        r"(?i)<\s*(?:br|hr)\s*/?\s*>|</?\s*(?:address|article|aside|blockquote|div|dl|dt|dd|figcaption|figure|footer|h[1-6]|header|li|main|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        value,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)

    lines = []
    for line in text.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines) or None


def _posted_at(epoch_ms):
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, timezone.utc).isoformat()


def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))

    for posting in postings:
        categories = posting.get("categories") or {}
        sections = []

        intro = posting.get("descriptionPlain") or _plain(posting.get("description"))
        if intro and intro.strip():
            sections.append(intro.strip())

        for item in posting.get("lists") or []:
            heading = (item.get("text") or "").strip()
            content = _plain(item.get("content"))
            section = "\n".join(part for part in (heading, content) if part)
            if section:
                sections.append(section)

        additional = posting.get("additionalPlain") or _plain(posting.get("additional"))
        if additional and additional.strip():
            sections.append(additional.strip())

        fields = {
            "url": posting.get("hostedUrl"),
            "title": posting.get("text"),
            "company": "Scality",
            "location": categories.get("location"),
            "locations": categories.get("allLocations"),
            "description": "\n\n".join(sections),
            "source": "Lever",
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("team"),
            "posted_at": _posted_at(posting.get("createdAt")),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        yield {key: value for key, value in fields.items() if value is not None}
