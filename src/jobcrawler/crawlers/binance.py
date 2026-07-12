
import html
import json
import re
from datetime import datetime, timezone


SITE = "https://api.lever.co/v0/postings/binance?mode=json"
EXTRA_HOSTS = []


def _plain(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)<li(?:\s[^>]*)?>", "\n- ", text)
    text = re.sub(
        r"(?i)</?(?:div|p|ul|ol|h1|h2|h3|h4|h5|h6)(?:\s[^>]*)?>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None


def _description(posting):
    sections = []
    intro = _plain(posting.get("description"))
    if intro:
        sections.append(intro)

    for section in posting.get("lists") or []:
        heading = (section.get("text") or "").strip()
        content = _plain(section.get("content"))
        if heading and content:
            sections.append(heading + "\n" + content)
        elif content:
            sections.append(content)
        elif heading:
            sections.append(heading)

    additional = _plain(posting.get("additional"))
    if additional:
        sections.append(additional)

    return "\n\n".join(sections)


def _posted_at(value):
    if value is None:
        return None
    return datetime.fromtimestamp(
        value / 1000,
        tz=timezone.utc,
    ).isoformat()


def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))

    for posting in postings:
        categories = posting.get("categories") or {}
        salary_range = posting.get("salaryRange") or {}

        fields = {
            "url": posting.get("hostedUrl"),
            "title": posting.get("text"),
            "company": "Binance",
            "location": categories.get("location") or (categories.get("allLocations") or [None])[0],
            "locations": categories.get("allLocations") or None,
            "description": _description(posting),
            "source": SITE,
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("team") or categories.get("department"),
            "posted_at": _posted_at(posting.get("createdAt")),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "salary_min": salary_range.get("min"),
            "salary_max": salary_range.get("max"),
            "salary_currency": salary_range.get("currency"),
            "salary_period": salary_range.get("interval"),
        }
        yield {
            key: value
            for key, value in fields.items()
            if value is not None
        }
