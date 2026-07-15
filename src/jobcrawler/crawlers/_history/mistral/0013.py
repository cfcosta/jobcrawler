
import html
import json
import re
from datetime import datetime, timezone


SITE = "https://api.lever.co/v0/postings/mistral?mode=json"
EXTRA_HOSTS = []


def _plain_text(value):
    if not value:
        return ""
    text = str(value)
    text = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/ul|/ol|/h[1-6]|/section|/tr)\s*/?\s*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _iso_time(epoch_ms):
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp(
        float(epoch_ms) / 1000.0, tz=timezone.utc
    ).isoformat().replace("+00:00", "Z")


def _full_description(posting):
    sections = []
    intro = _plain_text(posting.get("description"))
    if intro:
        sections.append(intro)

    for section in posting.get("lists") or []:
        heading = _plain_text(section.get("text"))
        content = _plain_text(section.get("content"))
        part = "\n\n".join(value for value in (heading, content) if value)
        if part:
            sections.append(part)

    additional = _plain_text(posting.get("additional"))
    if additional:
        sections.append(additional)

    return "\n\n".join(sections)


def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))
    for posting in postings:
        categories = posting.get("categories") or {}
        all_locations = categories.get("allLocations")

        fields = {
            "url": posting.get("hostedUrl"),
            "title": posting.get("text"),
            "company": "Mistral AI",
            "location": categories.get("location"),
            "description": _full_description(posting),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("team"),
            "posted_at": _iso_time(posting.get("createdAt")),
        }

        if isinstance(all_locations, list) and all_locations:
            fields["locations"] = all_locations

        compensation = posting.get("salaryRange") or {}
        if compensation.get("min") is not None:
            fields["salary_min"] = compensation["min"]
        if compensation.get("max") is not None:
            fields["salary_max"] = compensation["max"]
        if compensation.get("currency"):
            fields["salary_currency"] = compensation["currency"]
        if compensation.get("interval"):
            fields["salary_period"] = compensation["interval"]
        if posting.get("salaryDescription"):
            fields["salary"] = posting["salaryDescription"]

        yield Job(**{
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        })
