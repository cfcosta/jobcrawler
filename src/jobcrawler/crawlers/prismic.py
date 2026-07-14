import html
import json
import re
from datetime import datetime, timezone

SITE = "https://api.lever.co/v0/postings/prismic?mode=json"
EXTRA_HOSTS = []


def _plain_text(value):
    if not value:
        return None

    value = re.sub(
        r"(?i)<br\s*/?>|</(?:p|div|li|h[1-6]|tr|table|ul|ol|section)\s*>",
        "\n",
        value,
    )
    value = re.sub(r"(?i)<li(?:\s[^>]*)?>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _description(posting):
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

    return "\n\n".join(sections) or None


def _posted_at(epoch_ms):
    if epoch_ms is None:
        return None
    value = datetime.fromtimestamp(epoch_ms / 1000, timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def _salary_fields(posting):
    salary_range = posting.get("salaryRange") or {}
    fields = {}

    if salary_range.get("min") is not None:
        fields["salary_min"] = salary_range["min"]
    if salary_range.get("max") is not None:
        fields["salary_max"] = salary_range["max"]
    if salary_range.get("currency"):
        fields["salary_currency"] = salary_range["currency"]
    if salary_range.get("interval"):
        fields["salary_period"] = salary_range["interval"]

    return fields


def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))
    seen = set()

    for posting in postings:
        external_id = posting.get("id")
        url = posting.get("hostedUrl") or posting.get("applyUrl")
        identity = external_id or url
        if not identity or identity in seen:
            continue
        seen.add(identity)

        categories = posting.get("categories") or {}
        title = posting.get("text")
        location = categories.get("location")
        description = _description(posting)

        if not title or not url or not location or not description:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Prismic",
            "location": location,
            "description": description,
            "source": SITE,
        }

        optional = {
            "locations": categories.get("allLocations"),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("team"),
            "posted_at": _posted_at(posting.get("createdAt")),
            "external_id": external_id,
            "apply_url": posting.get("applyUrl"),
        }
        fields.update({
            key: value
            for key, value in optional.items()
            if value is not None and value != ""
        })
        fields.update(_salary_fields(posting))

        yield fields
