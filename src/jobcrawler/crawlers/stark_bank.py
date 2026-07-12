
import html
import json
import re
from datetime import datetime, timezone

SITE = "https://api.lever.co/v0/postings/starkbank?mode=json"
EXTRA_HOSTS = []


def _plain_text(value):
    if not value:
        return None

    text = html.unescape(value)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)<li(?:\s[^>]*)?>", "\n- ", text)
    text = re.sub(
        r"(?i)</?(?:div|p|ul|ol|section|article|h[1-6])(?:\s[^>]*)?>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _description(posting):
    sections = []

    intro = posting.get("descriptionPlain")
    if intro:
        sections.append(intro.strip())
    else:
        intro = _plain_text(posting.get("description"))
        if intro:
            sections.append(intro)

    for item in posting.get("lists") or []:
        heading = (item.get("text") or "").strip()
        content = _plain_text(item.get("content"))
        if heading and content:
            sections.append(heading + "\n" + content)
        elif content:
            sections.append(content)
        elif heading:
            sections.append(heading)

    additional = posting.get("additionalPlain")
    if additional:
        sections.append(additional.strip())
    else:
        additional = _plain_text(posting.get("additional"))
        if additional:
            sections.append(additional)

    return "\n\n".join(sections).strip()


def _posted_at(epoch_ms):
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp(
        epoch_ms / 1000,
        tz=timezone.utc,
    ).isoformat()


def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))

    for posting in postings:
        categories = posting.get("categories") or {}
        kwargs = {
            "url": posting.get("hostedUrl"),
            "title": posting.get("text"),
            "company": "Stark Bank",
            "source": SITE,
            "location": categories.get("location"),
            "description": _description(posting),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("team") or categories.get("department"),
            "posted_at": _posted_at(posting.get("createdAt")),
        }

        locations = categories.get("allLocations")
        if locations:
            kwargs["locations"] = locations

        yield {key: value for key, value in kwargs.items() if value is not None}
