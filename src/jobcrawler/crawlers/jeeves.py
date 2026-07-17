import json
from jobcrawler.runtime.contract import Job
import re
from datetime import datetime, timezone
from html import unescape


SITE = "https://api.lever.co/v0/postings/tryjeeves?mode=json"
EXTRA_HOSTS = []
COMPANY = "Jeeves"


def _plain(value):
    if not value:
        return None
    text = re.sub(r"(?i)<br\s*/?>|</?(?:p|div|li|ul|ol|section|h[1-6])(?:\s[^>]*)?>", "\n", value)
    text = re.sub(r"<[^>]+>", "", text)
    lines = []
    for line in unescape(text).splitlines():
        clean = " ".join(line.split())
        if clean:
            lines.append(clean)
    return "\n".join(lines) or None


def _description(posting):
    sections = []

    intro = posting.get("descriptionPlain") or _plain(posting.get("description"))
    if intro:
        sections.append(intro.strip())

    for section in posting.get("lists") or []:
        heading = (section.get("text") or "").strip()
        content = _plain(section.get("content"))
        block = "\n".join(part for part in (heading, content) if part)
        if block:
            sections.append(block)

    additional = posting.get("additionalPlain") or _plain(posting.get("additional"))
    if additional:
        sections.append(additional.strip())

    return "\n\n".join(sections)


def _posted_at(epoch_ms):
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).isoformat()


def crawl(ctx):
    response = ctx.fetch(SITE)
    postings = json.loads(response)

    for posting in postings:
        categories = posting.get("categories") or {}
        values = {
            "url": posting.get("hostedUrl"),
            "source": SITE,
            "title": posting.get("text"),
            "company": COMPANY,
            "location": categories.get("location"),
            "locations": categories.get("allLocations"),
            "description": _description(posting),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("department"),
            "posted_at": _posted_at(posting.get("createdAt")),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        yield Job(**{key: value for key, value in values.items()
                     if value is not None and value != "" and value != []})
