
import html
import json
import re

SITE = "https://apply.workable.com/api/v1/widget/accounts/yapily"
EXTRA_HOSTS = []


def _json(response):
    if isinstance(response, (str, bytes, bytearray)):
        return json.loads(response)
    if hasattr(response, "json"):
        return response.json()
    return json.loads(response.text)


def _clean(value):
    value = re.sub(
        r"(?i)<\s*br\s*/?\s*>|</?\s*(?:p|div|li|ul|ol|h[1-6]|section|"
        r"article|blockquote|tr|table)\b[^>]*>",
        "\n",
        value or "",
    )
    value = re.sub(r"(?is)<script\b[^>]*>.*?</script>", " ", value)
    value = re.sub(r"(?is)<style\b[^>]*>.*?</style>", " ", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def _place(item):
    parts = [
        item.get("city"),
        item.get("region") or item.get("state"),
        item.get("country"),
    ]
    return ", ".join(part for part in parts if part)


def crawl(ctx):
    board = _json(ctx.fetch(SITE + "?details=true"))

    for listing in board.get("jobs", []):
        description = _clean(listing.get("description"))
        shortcode = listing.get("shortcode")
        if not shortcode or not description:
            continue

        locations = []
        for place in listing.get("locations") or []:
            value = place.strip() if isinstance(place, str) else _place(place)
            if value and value not in locations:
                locations.append(value)

        location = _place(listing)
        if not location and locations:
            location = locations[0]

        values = {
            "url": listing.get("url") or listing.get("shortlink"),
            "title": listing.get("title"),
            "company": "Yapily",
            "location": location,
            "source": SITE,
            "description": description,
            "external_id": shortcode,
            "apply_url": listing.get("application_url"),
            "department": listing.get("department"),
            "employment_type": listing.get("employment_type"),
            "posted_at": listing.get("published_on"),
        }
        if locations:
            values["locations"] = locations
        if listing.get("telecommuting") is True:
            values["workplace_type"] = "remote"

        yield {
            key: value
            for key, value in values.items()
            if value is not None and value != ""
        }
