
import html
import json
import re
from datetime import datetime, timezone


SITE = "https://api.lever.co/v0/postings/certik?mode=json"
EXTRA_HOSTS = []


def _plain_text(value):
    if not value:
        return None
    text = re.sub(
        r"(?i)<\s*(?:br|div|p|li|ul|ol|section|article|h[1-6]|tr)\b[^>]*>",
        "\n",
        value,
    )
    text = re.sub(
        r"(?i)</\s*(?:div|p|li|ul|ol|section|article|h[1-6]|tr)\s*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None


def _full_description(posting):
    parts = []
    intro = (posting.get("descriptionPlain") or "").strip()
    if intro:
        parts.append(intro)

    for section in posting.get("lists") or []:
        heading = (section.get("text") or "").strip()
        body = _plain_text(section.get("content"))
        section_text = "\n".join(
            value for value in (heading, body) if value
        )
        if section_text:
            parts.append(section_text)

    additional = (posting.get("additionalPlain") or "").strip()
    if additional:
        parts.append(additional)

    return "\n\n".join(parts).strip()


def _posted_at(epoch_ms):
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp(
        epoch_ms / 1000, tz=timezone.utc
    ).isoformat().replace("+00:00", "Z")


def crawl(ctx):
    response = ctx.fetch(SITE)
    postings = json.loads(response)
    for posting in postings:
        categories = posting.get("categories") or {}
        kwargs = {
            "url": posting.get("hostedUrl"),
            "title": posting.get("text"),
            "company": "CertiK",
            "location": categories.get("location"),
            "description": _full_description(posting),
            "source": SITE,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("department") or categories.get("team"),
            "posted_at": _posted_at(posting.get("createdAt")),
        }

        locations = categories.get("allLocations")
        if locations:
            kwargs["locations"] = locations

        salary_range = posting.get("salaryRange") or {}
        if salary_range:
            kwargs["salary_min"] = salary_range.get("min")
            kwargs["salary_max"] = salary_range.get("max")
            kwargs["salary_currency"] = salary_range.get("currency")
            kwargs["salary_period"] = salary_range.get("interval")

        yield {
            key: value for key, value in kwargs.items()
            if value is not None
        }
