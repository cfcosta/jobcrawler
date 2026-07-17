
import html
import json
import re
from datetime import datetime, timezone

from jobcrawler.runtime.contract import Job


SITE = "https://api.lever.co/v0/postings/nibiru?mode=json"
EXTRA_HOSTS = []


def _plain(value):
    if not value:
        return None

    value = re.sub(
        r"(?i)<\s*li(?:\s[^>]*)?>",
        "\n- ",
        value,
    )
    value = re.sub(
        r"(?i)<\s*br\s*/?\s*>|</?\s*(?:div|p|ol|ul|h[1-6]|section|article|blockquote)(?:\s[^>]*)?>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _posted_at(epoch_ms):
    if not isinstance(epoch_ms, (int, float)):
        return None
    return datetime.fromtimestamp(
        epoch_ms / 1000, tz=timezone.utc
    ).isoformat().replace("+00:00", "Z")


def crawl(ctx):
    response = ctx.fetch(SITE)
    postings = json.loads(response)

    seen = set()
    for posting in postings:
        external_id = posting.get("id")
        url = posting.get("hostedUrl")
        identity = external_id or url
        if not identity or identity in seen:
            continue
        seen.add(identity)

        categories = posting.get("categories") or {}
        sections = []

        intro = _plain(posting.get("description"))
        if intro:
            sections.append(intro)

        for section in posting.get("lists") or []:
            heading = _plain(section.get("text"))
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

        description = "\n\n".join(sections).strip()
        if not description:
            continue

        values = {
            "url": url,
            "source": SITE,
            "title": posting.get("text"),
            "company": "Nibiru",
            "location": categories.get("location"),
            "locations": categories.get("allLocations"),
            "description": description,
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("team"),
            "posted_at": _posted_at(posting.get("createdAt")),
            "external_id": external_id,
            "apply_url": posting.get("applyUrl"),
        }

        salary_range = posting.get("salaryRange") or {}
        if salary_range:
            values["salary_min"] = salary_range.get("min")
            values["salary_max"] = salary_range.get("max")
            values["salary_currency"] = salary_range.get("currency")
            values["salary_period"] = salary_range.get("interval")

        salary = _plain(posting.get("salaryDescription"))
        if salary:
            values["salary"] = salary

        yield Job(**{
            key: value
            for key, value in values.items()
            if value is not None and value != ""
        })
