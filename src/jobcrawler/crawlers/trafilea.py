import html
import json
import re

from jobcrawler.runtime.contract import Job


SITE = "https://trafilea.recruitee.com/api/offers/"
EXTRA_HOSTS = []


def _html_to_text(value):
    if not value:
        return ""
    value = re.sub(
        r"(?i)<\s*(?:br|hr)\s*/?\s*>|</?\s*(?:address|article|aside|blockquote|div|h[1-6]|li|ol|p|section|table|td|th|tr|ul)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _published_at(value):
    if not value:
        return None
    if value.endswith(" UTC"):
        return value[:-4].replace(" ", "T", 1) + "+00:00"
    return value


def _workplace_type(offer):
    if offer.get("hybrid"):
        return "hybrid"
    if offer.get("remote"):
        return "remote"
    if offer.get("on_site"):
        return "on-site"
    return None


def crawl(ctx):
    response = ctx.fetch(SITE)
    payload = json.loads(response)

    for offer in payload.get("offers", []):
        if offer.get("status") != "published":
            continue

        sections = []
        for field in ("description", "requirements"):
            text = _html_to_text(offer.get(field))
            if text:
                sections.append(text)

        location_names = [
            item["name"]
            for item in offer.get("locations", [])
            if isinstance(item, dict) and item.get("name")
        ]

        yield Job(
            url=offer["careers_url"],
            title=offer["title"],
            company="Trafilea",
            location=offer["location"],
            locations=location_names or None,
            description="\n\n".join(sections),
            source=SITE,
            apply_url=offer.get("careers_apply_url"),
            department=offer.get("department"),
            employment_type=offer.get("employment_type_code"),
            workplace_type=_workplace_type(offer),
            posted_at=_published_at(offer.get("published_at")),
            external_id=str(offer["id"]),
        )
