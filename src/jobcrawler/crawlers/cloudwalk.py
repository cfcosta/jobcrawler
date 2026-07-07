
import json
import re
import html
from datetime import datetime, timezone

SITE = "https://api.lever.co/v0/postings/cloudwalk?mode=json"
EXTRA_HOSTS = []


def _clean_html(value):
    if not value:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|ul|ol)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return text or None


def _parts_to_description(posting):
    parts = []
    for key in ("description",):
        cleaned = _clean_html(posting.get(key))
        if cleaned:
            parts.append(cleaned)

    for section in posting.get("lists") or []:
        heading = _clean_html(section.get("text") or section.get("title"))
        body = _clean_html(section.get("content"))
        if heading and body:
            parts.append(heading + "\n" + body)
        elif body:
            parts.append(body)
        elif heading:
            parts.append(heading)

    additional = _clean_html(posting.get("additional"))
    if additional:
        parts.append(additional)

    return "\n\n".join(part for part in parts if part).strip()


def _posted_at(created_at):
    if created_at is None:
        return None
    try:
        return datetime.fromtimestamp(int(created_at) / 1000, tz=timezone.utc).date().isoformat()
    except Exception:
        return None


def _set_if_value(obj, key, value):
    if value is not None and value != "":
        obj[key] = value


def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))
    for posting in postings:
        categories = posting.get("categories") or {}

        job = {
            "url": posting.get("hostedUrl") or posting.get("applyUrl"),
            "title": posting.get("text"),
            "company": "CloudWalk",
            "location": categories.get("location"),
            "description": _parts_to_description(posting),
            "source": SITE,
        }

        _set_if_value(job, "external_id", posting.get("id"))
        _set_if_value(job, "apply_url", posting.get("applyUrl"))
        _set_if_value(job, "workplace_type", posting.get("workplaceType"))
        _set_if_value(job, "department", categories.get("team") or categories.get("department"))
        _set_if_value(job, "employment_type", categories.get("commitment"))
        _set_if_value(job, "posted_at", _posted_at(posting.get("createdAt")))

        salary = posting.get("salaryRange")
        if salary:
            _set_if_value(job, "salary", salary)

        yield job
