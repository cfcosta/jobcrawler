import json
import re
from datetime import datetime, timezone
from html import unescape



SITE = "https://api.lever.co/v0/postings/moonpay?mode=json"
EXTRA_HOSTS = []


def _plain_text(value):
    if not value:
        return None

    text = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/ul|/ol|/h[1-6]|/tr)\s*/?\s*>",
        "\n",
        value,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _response_text(response):
    if isinstance(response, bytes):
        return response.decode("utf-8")
    if isinstance(response, str):
        return response
    if hasattr(response, "text"):
        return response.text
    if hasattr(response, "content"):
        content = response.content
        if isinstance(content, bytes):
            return content.decode("utf-8")
        return content
    return str(response)


def _full_description(posting):
    fragments = []

    intro = posting.get("description") or posting.get("descriptionPlain")
    intro = _plain_text(intro)
    if intro:
        fragments.append(intro)

    for section in posting.get("lists") or []:
        section_parts = []
        heading = _plain_text(section.get("text"))
        content = _plain_text(section.get("content"))
        if heading:
            section_parts.append(heading)
        if content:
            section_parts.append(content)
        if section_parts:
            fragments.append("\n".join(section_parts))

    additional = posting.get("additional") or posting.get("additionalPlain")
    additional = _plain_text(additional)
    if additional:
        fragments.append(additional)

    return "\n\n".join(fragments).strip()


def _posted_at(epoch_ms):
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp(
        epoch_ms / 1000,
        timezone.utc,
    ).isoformat()


def crawl(ctx):
    postings = json.loads(_response_text(ctx.fetch(SITE)))

    for posting in postings:
        categories = posting.get("categories") or {}
        salary_range = posting.get("salaryRange") or {}

        fields = {
            "url": posting.get("hostedUrl"),
            "source": SITE,
            "title": posting.get("text"),
            "company": "MoonPay",
            "location": categories.get("location"),
            "locations": categories.get("allLocations") or None,
            "description": _full_description(posting),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("department") or categories.get("team"),
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
