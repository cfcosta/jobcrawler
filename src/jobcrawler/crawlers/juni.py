import html
import json
import re

SITE = "https://careers.juni.co/jobs.json"
EXTRA_HOSTS = []


def _plain_text(value):
    value = value or ""
    value = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/h[1-6]|/tr|/section|/article|/ul|/ol)\s*/?\s*>",
        "\n",
        value,
    )
    value = re.sub(r"(?is)<script\b[^>]*>.*?</script\s*>", "", value)
    value = re.sub(r"(?is)<style\b[^>]*>.*?</style\s*>", "", value)
    value = re.sub(r"(?s)<[^>]+>", "", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        clean = " ".join(line.split())
        if clean:
            lines.append(clean)
    return "\n".join(lines)


def _address_text(place):
    address = place.get("address") or {}
    parts = [
        address.get("streetAddress"),
        address.get("postalCode"),
        address.get("addressLocality"),
        address.get("addressRegion"),
        address.get("addressCountry"),
    ]
    return ", ".join(
        str(part).strip()
        for part in parts
        if part is not None and str(part).strip()
    )


def crawl(ctx):
    feed = json.loads(ctx.fetch(SITE))

    for item in feed.get("items", []):
        posting = item.get("_jobposting") or {}
        organization = posting.get("hiringOrganization") or {}
        identifier = posting.get("identifier") or {}

        locations = [
            location
            for location in (
                _address_text(place)
                for place in posting.get("jobLocation", [])
            )
            if location
        ]

        fields = {
            "url": item.get("url"),
            "title": item.get("title") or posting.get("title"),
            "company": organization.get("name") or "Juni Technology",
            "location": locations[0] if locations else None,
            "description": _plain_text(
                item.get("content_html") or posting.get("description")
            ),
            "source": SITE,
        }

        if locations:
            fields["locations"] = locations

        external_id = identifier.get("value")
        if external_id is not None:
            fields["external_id"] = str(external_id)

        posted_at = posting.get("datePosted") or item.get("date_published")
        if posted_at:
            fields["posted_at"] = posted_at

        if item.get("url"):
            fields["apply_url"] = item["url"]

        yield fields
