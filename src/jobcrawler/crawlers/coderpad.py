from datetime import datetime, timezone
from html import unescape
import json
import re

SITE = "https://api.lever.co/v0/postings/coderpad?mode=json"
EXTRA_HOSTS = []


def _text(value):
    if not value:
        return None
    text = unescape(value)
    text = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/ul|/ol|/h[1-6]|/tr|/table)\s*/?\s*>",
        "\n",
        text,
    )
    text = re.sub(r"(?i)<\s*li(?:\s[^>]*)?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _description(posting):
    parts = []
    intro = _text(posting.get("description"))
    if intro:
        parts.append(intro)

    for section in posting.get("lists") or []:
        heading = _text(section.get("text"))
        content = _text(section.get("content"))
        if heading:
            parts.append(heading)
        if content:
            parts.append(content)

    additional = _text(posting.get("additional"))
    if additional:
        parts.append(additional)

    return "\n\n".join(parts)


def _iso_timestamp(epoch_ms):
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, timezone.utc).isoformat()


def crawl(ctx):
    payload = ctx.fetch(SITE)
    if not isinstance(payload, str):
        payload = payload.text

    for posting in json.loads(payload):
        categories = posting.get("categories") or {}
        salary_range = posting.get("salaryRange") or {}
        salary_text = _text(
            posting.get("salaryDescriptionPlain")
            or posting.get("salaryDescription")
        )

        fields = {
            "url": posting.get("hostedUrl"),
            "title": posting.get("text"),
            "company": "CoderPad",
            "location": categories.get("location"),
            "description": _description(posting),
            "source": SITE,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("team"),
            "posted_at": _iso_timestamp(posting.get("createdAt")),
        }

        all_locations = categories.get("allLocations")
        if all_locations:
            fields["locations"] = all_locations

        if salary_range:
            fields["salary_min"] = salary_range.get("min")
            fields["salary_max"] = salary_range.get("max")
            fields["salary_currency"] = salary_range.get("currency")
            fields["salary_period"] = salary_range.get("interval")

        if salary_text:
            fields["salary"] = salary_text

        yield {
            key: value for key, value in fields.items() if value is not None
        }
