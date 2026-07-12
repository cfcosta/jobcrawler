
import html
import json
import re
from datetime import datetime, timezone

SITE = 'https://api.lever.co/v0/postings/cloudinary?mode=json'
EXTRA_HOSTS = []


def _text(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"</(?:p|div|li|h[1-6]|ul|ol)>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)
    value = value.replace("\r", "")
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = re.sub(r"[ \t]{2,}", " ", value)
    return value.strip() or None


def _full_description(posting):
    parts = []

    intro = posting.get("descriptionPlain") or posting.get("description")
    if intro:
        parts.append(_text(intro))

    for section in posting.get("lists") or []:
        heading = _text(section.get("text"))
        content = _text(section.get("content"))
        if heading and content:
            parts.append(heading + "\n" + content)
        elif content:
            parts.append(content)

    additional = posting.get("additionalPlain") or posting.get("additional")
    if additional:
        parts.append(_text(additional))

    return "\n\n".join(part for part in parts if part)


def _posted_at(value):
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()


def crawl(ctx):
    payload = ctx.fetch(SITE)
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        payload = json.loads(payload)

    for posting in payload:
        categories = posting.get("categories") or {}
        kwargs = {
            "url": posting.get("hostedUrl"),
            "source": SITE,
            "title": posting.get("text"),
            "company": "Cloudinary",
            "location": categories.get("location"),
            "description": _full_description(posting),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("team"),
            "posted_at": _posted_at(posting.get("createdAt")),
        }

        locations = categories.get("allLocations")
        if locations:
            kwargs["locations"] = locations

        yield {key: value for key, value in kwargs.items() if value is not None}
