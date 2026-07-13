
import json
import re
from datetime import datetime, timezone
from html import unescape


SITE = "https://api.lever.co/v0/postings/pipedrive?mode=json"
EXTRA_HOSTS = []


def _text(value):
    if not value:
        return None

    text = re.sub(r"(?i)<li(?:\s[^>]*)?>", "\n- ", value)
    text = re.sub(
        r"(?i)</?(?:br|div|p|ul|ol|li|h[1-6]|section|article|tr|table)"
        r"(?:\s[^>]*)?>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text).replace("\xa0", " ")

    lines = []
    for line in text.splitlines():
        clean = " ".join(line.split())
        if clean:
            lines.append(clean)
    return "\n".join(lines) or None


def _description(posting):
    sections = []

    intro = _text(posting.get("description"))
    if intro:
        sections.append(intro)

    for section in posting.get("lists") or []:
        heading = _text(section.get("text"))
        content = _text(section.get("content"))
        block = "\n".join(
            part for part in (heading, content) if part
        )
        if block:
            sections.append(block)

    additional = _text(posting.get("additional"))
    if additional:
        sections.append(additional)

    return "\n\n".join(sections)


def _posted_at(epoch_ms):
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp(
        epoch_ms / 1000,
        timezone.utc,
    ).isoformat()


def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))
    seen = set()

    for posting in postings:
        external_id = posting.get("id")
        url = posting.get("hostedUrl")
        dedupe_key = external_id or url

        if not dedupe_key or dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        categories = posting.get("categories") or {}
        job = {
            "url": url,
            "title": posting.get("text"),
            "company": "Pipedrive",
            "location": categories.get("location"),
            "description": _description(posting),
            "source": SITE,
            "external_id": external_id,
            "apply_url": posting.get("applyUrl"),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("team") or categories.get("department"),
            "posted_at": _posted_at(posting.get("createdAt")),
        }

        locations = categories.get("allLocations")
        if locations:
            job["locations"] = locations

        yield {
            key: value
            for key, value in job.items()
            if value is not None
        }
