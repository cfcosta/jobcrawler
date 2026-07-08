
import datetime
import html
import json
import re

SITE = "https://api.lever.co/v0/postings/celestia?mode=json"
EXTRA_HOSTS = []


def _clean_html(value):
    if not value:
        return None
    text = str(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(div|p|li|ul|ol|h[1-6])\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li[^>]*>", "- ", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines) or None


def _posted_at(epoch_ms):
    if epoch_ms is None:
        return None
    try:
        return datetime.datetime.fromtimestamp(int(epoch_ms) / 1000, datetime.timezone.utc).date().isoformat()
    except Exception:
        return None


def _full_description(posting):
    parts = []
    intro = _clean_html(posting.get("description"))
    if intro:
        parts.append(intro)

    for item in posting.get("lists") or []:
        heading = _clean_html(item.get("text"))
        content = _clean_html(item.get("content"))
        block = "\n".join(part for part in [heading, content] if part)
        if block:
            parts.append(block)

    additional = _clean_html(posting.get("additional"))
    if additional:
        parts.append(additional)

    return "\n\n".join(parts) or None


def crawl(ctx):
    raw = ctx.fetch(SITE)
    if not isinstance(raw, str):
        raw = raw.text
    postings = json.loads(raw)

    for posting in postings:
        categories = posting.get("categories") or {}
        job = {
            "url": posting.get("hostedUrl"),
            "title": posting.get("text"),
            "company": "Celestia Labs",
            "location": categories.get("location"),
            "description": _full_description(posting),
            "source": SITE,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl") or posting.get("hostedUrl"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("team"),
            "employment_type": categories.get("commitment"),
            "posted_at": _posted_at(posting.get("createdAt")),
        }
        yield {key: value for key, value in job.items() if value not in (None, "")}
