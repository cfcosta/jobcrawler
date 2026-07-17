
from datetime import datetime, timezone
from html import unescape
import json
from jobcrawler.runtime.contract import Job
import re


SITE = "https://api.lever.co/v0/postings/entefy?mode=json"
EXTRA_HOSTS = []


def _text(value):
    if not value:
        return None

    text = re.sub(r"(?i)<br\s*/?>", "\n", value)
    text = re.sub(r"(?i)<li[^>]*>", "\n- ", text)
    text = re.sub(
        r"(?i)</?(?:div|p|ul|ol|li|section|article|h[1-6])[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)

    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines) or None


def _posted_at(epoch_ms):
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp(
        epoch_ms / 1000, tz=timezone.utc
    ).isoformat()


def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))

    for posting in postings:
        categories = posting.get("categories") or {}
        sections = []

        intro = _text(
            posting.get("descriptionPlain") or posting.get("description")
        )
        if intro:
            sections.append(intro)

        for item in posting.get("lists") or []:
            heading = _text(item.get("text"))
            content = _text(item.get("content"))
            if heading and content:
                sections.append(heading + "\n" + content)
            elif heading or content:
                sections.append(heading or content)

        additional = _text(
            posting.get("additionalPlain") or posting.get("additional")
        )
        if additional:
            sections.append(additional)

        fields = {
            "url": posting["hostedUrl"],
            "title": posting["text"],
            "company": "Entefy",
            "location": categories.get("location"),
            "description": "\n\n".join(sections),
            "source": SITE,
        }

        optional = {
            "locations": categories.get("allLocations"),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("team") or categories.get("department"),
            "posted_at": _posted_at(posting.get("createdAt")),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        fields.update({
            key: value
            for key, value in optional.items()
            if value is not None and value != "" and value != []
        })

        yield Job(**fields)
