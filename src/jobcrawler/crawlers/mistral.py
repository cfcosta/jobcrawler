
import json
import re
from datetime import datetime, timezone
from html import unescape


SITE = "https://api.lever.co/v0/postings/mistral?mode=json"
EXTRA_HOSTS = []


def _plain_text(value):
    if not value:
        return None

    text = re.sub(
        r"(?i)<br\s*/?>|</(?:p|div|li|ul|ol|h[1-6]|section|article|tr)>",
        "\n",
        value,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _full_description(posting):
    sections = []

    intro = _plain_text(posting.get("description"))
    if intro:
        sections.append(intro)

    for section in posting.get("lists") or []:
        heading = _plain_text(section.get("text"))
        content = _plain_text(section.get("content"))
        if heading and content:
            sections.append(heading + "\n" + content)
        elif heading:
            sections.append(heading)
        elif content:
            sections.append(content)

    additional = _plain_text(posting.get("additional"))
    if additional:
        sections.append(additional)

    return "\n\n".join(sections).strip()


def _iso_timestamp(epoch_ms):
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp(
        epoch_ms / 1000, tz=timezone.utc
    ).isoformat().replace("+00:00", "Z")


def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))

    for posting in postings:
        categories = posting.get("categories") or {}
        location = categories.get("location")
        locations = categories.get("allLocations")

        if isinstance(locations, str):
            locations = [locations]
        elif locations:
            locations = [item for item in locations if item]
        else:
            locations = None

        if not location and locations:
            location = locations[0]

        fields = {
            "url": posting.get("hostedUrl"),
            "title": posting.get("text"),
            "company": "Mistral AI",
            "location": location,
            "description": _full_description(posting),
            "source": SITE,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "department": categories.get("team"),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "posted_at": _iso_timestamp(posting.get("createdAt")),
        }
        if locations:
            fields["locations"] = locations

        yield Job(**{key: value for key, value in fields.items() if value is not None})
