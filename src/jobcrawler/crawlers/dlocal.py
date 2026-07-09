
import json
import re
import html
from datetime import datetime, timezone

SITE = "https://api.lever.co/v0/postings/dlocal?mode=json"
EXTRA_HOSTS = []


def _html_to_text(value):
    if not value:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|section|article|tr)\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li[^>]*>", "\n- ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _posted_at(ms):
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return None


def _description(job):
    parts = []
    if job.get("description"):
        parts.append(job.get("description"))
    for section in job.get("lists") or []:
        heading = section.get("text")
        content = section.get("content")
        if heading:
            parts.append("<h3>%s</h3>" % html.escape(str(heading)))
        if content:
            parts.append(content)
    if job.get("additional"):
        parts.append(job.get("additional"))
    return _html_to_text("\n".join(parts))


def _clean(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def crawl(ctx):
    raw = ctx.fetch(SITE)
    postings = json.loads(raw)
    for job in postings:
        categories = job.get("categories") or {}
        title = _clean(job.get("text"))
        url = _clean(job.get("hostedUrl"))
        location = _clean(categories.get("location"))
        description = _description(job)
        if not (title and url and location and description):
            continue

        item = {
            "url": url,
            "title": title,
            "company": "dLocal",
            "location": location,
            "description": description,
            "source": SITE,
        }

        fields = {
            "external_id": job.get("id"),
            "apply_url": job.get("applyUrl"),
            "posted_at": _posted_at(job.get("createdAt")),
            "workplace_type": job.get("workplaceType"),
            "employment_type": categories.get("commitment"),
            "department": categories.get("team") or categories.get("department"),
        }
        for key, value in fields.items():
            value = _clean(value)
            if value:
                item[key] = value

        yield item
