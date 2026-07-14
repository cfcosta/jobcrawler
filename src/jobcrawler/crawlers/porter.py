
import html
import json
import re
from datetime import datetime, timezone


SITE = "https://api.lever.co/v0/postings/porter?mode=json"
EXTRA_HOSTS = []


def _plain_text(fragment):
    if not fragment:
        return None
    value = re.sub(
        r"<\s*(?:br|/p|/div|/li|/ul|/ol|/h[1-6])\s*/?\s*>",
        "\n",
        fragment,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"<\s*li(?:\s[^>]*)?>", "\n- ", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"[ \t]{2,}", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    return value or None


def _posted_at(epoch_ms):
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp(
        epoch_ms / 1000, tz=timezone.utc
    ).isoformat().replace("+00:00", "Z")


def _full_description(posting):
    fragments = [posting.get("description")]
    fragments.extend(
        section.get("content")
        for section in (posting.get("lists") or [])
        if isinstance(section, dict)
    )
    fragments.append(posting.get("additional"))

    sections = [_plain_text(fragment) for fragment in fragments if fragment]
    return "\n\n".join(section for section in sections if section).strip()


def crawl(ctx):
    response = ctx.fetch(SITE)
    postings = json.loads(response)
    jobs = []

    for posting in postings:
        categories = posting.get("categories") or {}
        location = categories.get("location")
        hosted_url = posting.get("hostedUrl")
        description = _full_description(posting)

        if not posting.get("text") or not hosted_url or not location or not description:
            continue

        fields = {
            "url": hosted_url,
            "title": posting["text"],
            "company": "Porter",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("team"),
            "posted_at": _posted_at(posting.get("createdAt")),
        }

        locations = categories.get("allLocations")
        if isinstance(locations, list):
            locations = [
                item for item in locations
                if isinstance(item, str) and item
            ]
            if locations:
                fields["locations"] = locations

        salary_range = posting.get("salaryRange") or {}
        if salary_range.get("min") is not None:
            fields["salary_min"] = salary_range["min"]
        if salary_range.get("max") is not None:
            fields["salary_max"] = salary_range["max"]
        if salary_range.get("currency"):
            fields["salary_currency"] = salary_range["currency"]
        if salary_range.get("interval"):
            fields["salary_period"] = salary_range["interval"]

        salary_text = posting.get("salaryDescriptionPlain")
        if salary_text:
            salary_text = salary_text.strip()
        elif posting.get("salaryDescription"):
            salary_text = _plain_text(posting["salaryDescription"])
        if salary_text:
            fields["salary"] = salary_text

        jobs.append({
            key: value
            for key, value in fields.items()
            if value is not None and value != "" and value != []
        })

    return jobs
