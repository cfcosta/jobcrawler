
import html
import json
import re


SITE = "https://aikidosecurity.recruitee.com/api/offers/"
EXTRA_HOSTS = []


def _plain_text(*fragments):
    parts = []
    for fragment in fragments:
        if not fragment:
            continue
        value = html.unescape(fragment)
        value = re.sub(
            r"(?i)</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|"
            r"figcaption|figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|"
            r"pre|section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
            "\n",
            value,
        )
        value = re.sub(r"<[^>]+>", "", value)
        for line in value.splitlines():
            cleaned = " ".join(line.split())
            if cleaned:
                parts.append(cleaned)
    return "\n".join(parts)


def _timestamp(value):
    if not value:
        return None
    if value.endswith(" UTC"):
        return value[:-4].replace(" ", "T", 1) + "+00:00"
    return value


def _workplace_type(offer):
    if offer.get("remote"):
        return "remote"
    if offer.get("hybrid"):
        return "hybrid"
    if offer.get("on_site"):
        return "on-site"
    return None


def crawl(ctx):
    response = None
    last_error = None
    for attempt in range(3):
        try:
            response = ctx.fetch(SITE)
            break
        except Exception as error:
            last_error = error

    if response is None:
        raise last_error

    data = json.loads(response)

    for offer in data.get("offers", []):
        if offer.get("status") != "published":
            continue

        description = _plain_text(
            offer.get("description"),
            offer.get("requirements"),
        )
        if not offer.get("title") or not offer.get("careers_url") or not description:
            continue

        location_names = [
            location["name"]
            for location in offer.get("locations", [])
            if isinstance(location, dict) and location.get("name")
        ]

        salary = offer.get("salary") or {}
        kwargs = {
            "url": offer["careers_url"],
            "title": offer["title"],
            "company": "Aikido Security",
            "location": offer.get("location") or None,
            "description": description,
            "source": "Recruitee",
            "external_id": str(offer["id"]) if offer.get("id") is not None else None,
            "apply_url": offer.get("careers_apply_url") or None,
            "department": offer.get("department") or None,
            "employment_type": offer.get("employment_type_code") or None,
            "workplace_type": _workplace_type(offer),
            "posted_at": _timestamp(offer.get("published_at")),
            "expires_at": _timestamp(offer.get("close_at")),
            "locations": location_names or None,
            "salary_min": salary.get("min"),
            "salary_max": salary.get("max"),
            "salary_currency": salary.get("currency") or None,
            "salary_period": salary.get("period") or None,
        }

        yield {key: value for key, value in kwargs.items() if value is not None}
