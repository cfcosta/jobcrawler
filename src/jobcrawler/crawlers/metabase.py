
import json
import re
from datetime import datetime, timezone
from html import unescape



SITE = "https://api.lever.co/v0/postings/metabase?mode=json"
EXTRA_HOSTS = []


def _plain(value):
    if not value:
        return None
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(
        r"(?i)</?(?:div|p|ul|ol|h[1-6]|section|article|blockquote|pre)[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?i)<li[^>]*>", "\n- ", value)
    value = re.sub(r"(?i)</li\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"[ \t]{2,}", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _posted_at(epoch_ms):
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp(
        epoch_ms / 1000, tz=timezone.utc
    ).isoformat().replace("+00:00", "Z")


def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))

    for posting in postings:
        categories = posting.get("categories") or {}
        sections = [posting.get("description")]
        sections.extend(
            item.get("content")
            for item in (posting.get("lists") or [])
            if item.get("content")
        )
        sections.append(posting.get("additional"))

        description = "\n\n".join(
            text for text in (_plain(section) for section in sections) if text
        )

        kwargs = {
            "url": posting.get("hostedUrl"),
            "title": posting.get("text"),
            "company": "Metabase",
            "location": categories.get("location"),
            "description": description,
            "source": SITE,
        }

        optional = {
            "locations": categories.get("allLocations"),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("team"),
            "posted_at": _posted_at(posting.get("createdAt")),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }

        salary_range = posting.get("salaryRange") or {}
        if salary_range:
            optional.update({
                "salary_min": salary_range.get("min"),
                "salary_max": salary_range.get("max"),
                "salary_currency": salary_range.get("currency"),
                "salary_period": salary_range.get("interval"),
            })

        if posting.get("salaryDescriptionPlain"):
            optional["salary"] = posting.get("salaryDescriptionPlain")

        kwargs.update({
            key: value
            for key, value in optional.items()
            if value is not None and value != "" and value != []
        })
        yield kwargs
