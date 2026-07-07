
import json
import re
from datetime import datetime, timezone
from html import unescape

SITE = "https://api.lever.co/v0/postings/neon?mode=json"
EXTRA_HOSTS = []


def _html_to_text(value):
    if not value:
        return None
    text = str(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6])\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*(p|div|li|ul|ol|h[1-6])(?:\s[^>]*)?>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\r", "\n", text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _plain_or_html(post, plain_key, html_key):
    return post.get(plain_key) or _html_to_text(post.get(html_key))


def _created_at(ms):
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).date().isoformat()
    except Exception:
        return None


def _full_description(post):
    sections = []

    intro = _plain_or_html(post, "descriptionPlain", "description")
    if intro:
        sections.append(intro)

    for item in post.get("lists") or []:
        title = item.get("text")
        body = _html_to_text(item.get("content")) or item.get("content")
        block = "\n\n".join(part.strip() for part in (title, body) if part and part.strip())
        if block:
            sections.append(block)

    additional = _plain_or_html(post, "additionalPlain", "additional")
    if additional:
        sections.append(additional)

    text = "\n\n".join(part.strip() for part in sections if part and part.strip())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None


def _job_dict(post):
    categories = post.get("categories") or {}
    job = {
        "url": post.get("hostedUrl"),
        "title": post.get("text"),
        "company": "Neon",
        "location": categories.get("location"),
        "description": _full_description(post),
        "source": SITE,
    }

    optional = {
        "external_id": post.get("id"),
        "apply_url": post.get("applyUrl"),
        "posted_at": _created_at(post.get("createdAt")),
        "workplace_type": post.get("workplaceType"),
        "employment_type": categories.get("commitment"),
        "department": categories.get("team") or categories.get("department"),
    }
    for key, value in optional.items():
        if value:
            job[key] = value

    return {key: value for key, value in job.items() if value not in (None, "")}


def crawl(ctx):
    response = ctx.fetch(SITE)
    raw = response.text if hasattr(response, "text") else response
    for post in json.loads(raw):
        job = _job_dict(post)
        if job.get("url") and job.get("title") and job.get("description"):
            yield job
