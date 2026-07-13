
import json
import re
from datetime import datetime, timezone
from html import unescape

SITE = "https://api.lever.co/v0/postings/easypost-2?mode=json"
EXTRA_HOSTS = []


def _plain(value):
    if not value:
        return None
    text = unescape(value)
    text = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/h[1-6]|/section|/article|/tr|/ul|/ol)\s*/?\s*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    lines = (" ".join(line.split()) for line in unescape(text).splitlines())
    result = "\n".join(line for line in lines if line)
    return result or None


def _posted_at(value):
    if value is None:
        return None
    return datetime.fromtimestamp(
        value / 1000, tz=timezone.utc
    ).isoformat().replace("+00:00", "Z")


def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))

    for posting in postings:
        categories = posting.get("categories") or {}
        sections = [posting.get("description")]
        sections.extend(
            item.get("content") for item in (posting.get("lists") or [])
        )
        sections.append(posting.get("additional"))
        description = "\n\n".join(
            text for text in (_plain(section) for section in sections) if text
        )

        values = {
            "url": posting.get("hostedUrl"),
            "source": SITE,
            "title": posting.get("text"),
            "company": "EasyPost",
            "location": categories.get("location"),
            "description": description,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("department") or categories.get("team"),
            "posted_at": _posted_at(posting.get("createdAt")),
        }

        locations = categories.get("allLocations")
        if locations:
            values["locations"] = locations

        salary_range = posting.get("salaryRange") or {}
        if salary_range:
            values["salary_min"] = salary_range.get("min")
            values["salary_max"] = salary_range.get("max")
            values["salary_currency"] = salary_range.get("currency")
            values["salary_period"] = salary_range.get("interval")
            printed_salary = posting.get("salaryDescriptionPlain")
            if printed_salary:
                values["salary"] = printed_salary

        yield {
            key: value for key, value in values.items()
            if value is not None
        }
