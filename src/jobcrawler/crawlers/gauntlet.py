
import html
import json
import re
from datetime import datetime, timezone

SITE = "https://api.lever.co/v0/postings/gauntlet?mode=json"
EXTRA_HOSTS = []


def _strip_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|ul|ol)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _full_description(posting):
    parts = []

    intro = _strip_html(posting.get("description"))
    if intro:
        parts.append(intro)

    for item in posting.get("lists") or []:
        heading = _strip_html(item.get("text"))
        content = _strip_html(item.get("content"))
        if heading and content:
            parts.append(heading + "\n" + content)
        elif content:
            parts.append(content)
        elif heading:
            parts.append(heading)

    additional = _strip_html(posting.get("additional"))
    if additional:
        parts.append(additional)

    return "\n\n".join(parts).strip() or None


def _iso_from_epoch_ms(value):
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).isoformat()
    except Exception:
        return None


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for posting in data:
        categories = posting.get("categories") or {}

        title = posting.get("text") or posting.get("title")
        url = posting.get("hostedUrl") or posting.get("applyUrl")
        description = _full_description(posting)
        location = categories.get("location")

        job = {
            "url": url,
            "title": title,
            "company": "Gauntlet",
            "location": location,
            "description": description,
            "source": SITE,
        }

        optional_fields = {
            "department": categories.get("team"),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl") or posting.get("hostedUrl"),
            "posted_at": _iso_from_epoch_ms(posting.get("createdAt")),
        }
        for key, value in optional_fields.items():
            if value not in (None, ""):
                job[key] = value

        if all(job.get(k) for k in ("url", "title", "company", "location", "description")):
            yield job
