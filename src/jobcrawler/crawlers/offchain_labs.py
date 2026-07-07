
import json
import re
import html
from datetime import datetime, timezone

SITE = "https://api.lever.co/v0/postings/offchainlabs?mode=json"

_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_RE = re.compile(r"(?i)</?(?:div|p|br|li|ul|ol|h[1-6])[^>]*>")

def _clean_text(value):
    if value is None:
        return None
    text = str(value)
    text = _BLOCK_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None

def _join_parts(parts):
    cleaned = []
    seen = set()
    for part in parts:
        text = _clean_text(part)
        if text and text not in seen:
            cleaned.append(text)
            seen.add(text)
    return "\n\n".join(cleaned).strip()

def _posted_at(value):
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).date().isoformat()
    except Exception:
        return None

def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))
    for posting in postings:
        categories = posting.get("categories") or {}

        body_parts = [posting.get("descriptionPlain") or posting.get("description")]
        for item in posting.get("lists") or []:
            body_parts.append(item.get("text"))
            body_parts.append(item.get("content"))
        body_parts.append(posting.get("additionalPlain") or posting.get("additional"))

        description = _join_parts(body_parts)
        if not description:
            continue

        job = {
            "url": posting.get("hostedUrl"),
            "title": posting.get("text"),
            "company": "Offchain Labs",
            "location": categories.get("location"),
            "description": description,
            "source": SITE,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "department": categories.get("team"),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "posted_at": _posted_at(posting.get("createdAt")),
        }
        yield {key: value for key, value in job.items() if value not in (None, "")}
