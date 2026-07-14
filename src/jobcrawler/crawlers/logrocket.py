import html
import json
import re
from datetime import datetime, timezone


SITE = "https://api.lever.co/v0/postings/logrocket?mode=json"
EXTRA_HOSTS = []


def _plain_text(value):
    if not value:
        return None
    text = str(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li(?:\s[^>]*)?>", "\n- ", text)
    text = re.sub(
        r"(?i)</?\s*(?:address|article|aside|blockquote|div|dl|dt|dd|figcaption|figure|footer|h[1-6]|header|hr|main|ol|p|pre|section|table|tbody|td|th|thead|tr|ul)(?:\s[^>]*)?>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _full_description(posting):
    sections = []
    intro = _plain_text(posting.get("description"))
    if intro:
        sections.append(intro)

    for item in posting.get("lists") or []:
        heading = _plain_text(item.get("text"))
        content = _plain_text(item.get("content"))
        section = "\n".join(part for part in (heading, content) if part)
        if section:
            sections.append(section)

    additional = _plain_text(posting.get("additional"))
    if additional:
        sections.append(additional)
    return "\n\n".join(sections)


def _posted_at(epoch_ms):
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).isoformat()


def crawl(ctx):
    postings = ctx.fetch(SITE)
    if isinstance(postings, str):
        postings = json.loads(postings)

    for posting in postings:
        categories = posting.get("categories") or {}
        salary_range = posting.get("salaryRange") or {}
        values = {
            "url": posting.get("hostedUrl"),
            "source": SITE,
            "title": posting.get("text"),
            "company": "LogRocket",
            "location": categories.get("location"),
            "locations": categories.get("allLocations") or None,
            "description": _full_description(posting),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("team"),
            "posted_at": _posted_at(posting.get("createdAt")),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "salary_min": salary_range.get("min"),
            "salary_max": salary_range.get("max"),
            "salary_currency": salary_range.get("currency"),
            "salary_period": salary_range.get("interval"),
        }
        yield {key: value for key, value in values.items() if value is not None}
