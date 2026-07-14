
import html
import json
import re
from datetime import datetime, timezone

SITE = "https://api.lever.co/v0/postings/dwolla?mode=json"
EXTRA_HOSTS = []
API_URL = "https://api.lever.co/v0/postings/dwolla"


def _html_to_text(value):
    if not value:
        return ""
    value = re.sub(
        r"(?i)<\s*(br|/p|/div|/li|/h[1-6])(?:\s[^>]*)?>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line)


def _text(plain, markup):
    return plain.strip() if plain else _html_to_text(markup)


def _list_section(section):
    title = (section.get("text") or "").strip()
    content = _text(section.get("contentPlain"), section.get("content"))
    if title and content:
        return title + "\n" + content
    return title or content


def _set(data, key, value):
    if value is not None and value != "" and value != [] and value != {}:
        data[key] = value


def crawl(ctx):
    postings = json.loads(ctx.fetch(API_URL))

    for posting in postings:
        categories = posting.get("categories") or {}

        parts = []
        intro = _text(posting.get("descriptionPlain"), posting.get("description"))
        if intro:
            parts.append(intro)
        for section in posting.get("lists") or []:
            section_text = _list_section(section)
            if section_text:
                parts.append(section_text)
        additional = _text(
            posting.get("additionalPlain"),
            posting.get("additional"),
        )
        if additional:
            parts.append(additional)

        data = {
            "url": posting.get("hostedUrl"),
            "title": posting.get("text"),
            "company": "Dwolla",
            "location": categories.get("location"),
            "source": SITE,
            "description": "\n\n".join(parts),
        }
        _set(data, "locations", categories.get("allLocations"))
        _set(data, "external_id", posting.get("id"))
        _set(data, "apply_url", posting.get("applyUrl") or posting.get("hostedUrl"))
        _set(data, "employment_type", categories.get("commitment"))
        _set(data, "workplace_type", posting.get("workplaceType"))
        _set(data, "department", categories.get("department") or categories.get("team"))

        created_at = posting.get("createdAt")
        if created_at is not None:
            data["posted_at"] = datetime.fromtimestamp(
                created_at / 1000,
                tz=timezone.utc,
            ).isoformat()

        salary = posting.get("salaryRange") or {}
        _set(data, "salary_min", salary.get("min"))
        _set(data, "salary_max", salary.get("max"))
        _set(data, "salary_currency", salary.get("currency"))
        _set(data, "salary_period", salary.get("interval"))

        yield data
