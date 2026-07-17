
import html
import json
import re

from jobcrawler.runtime.contract import Job


SITE = "https://careers.juni.co/jobs.json"
EXTRA_HOSTS = []


def _strip_html(value):
    value = value or ""
    value = re.sub(
        r"(?i)</?(?:br|div|h[1-6]|li|ol|p|section|table|tr|ul)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?is)<script\b[^>]*>.*?</script>", "", value)
    value = re.sub(r"(?is)<style\b[^>]*>.*?</style>", "", value)
    value = re.sub(r"(?s)<[^>]+>", "", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _format_location(place):
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
    # The query avoids a stale cached empty representation of the same JSON Feed.
    feed = json.loads(ctx.fetch(SITE + "?crawler=1"))
    seen = set()

    for item in feed.get("items") or []:
        posting = item.get("_jobposting") or {}
        url = item.get("url")
        title = item.get("title") or posting.get("title")
        description = _strip_html(
            item.get("content_html") or posting.get("description")
        )

        identifier = posting.get("identifier") or {}
        external_id = identifier.get("value")
        dedupe_key = str(external_id) if external_id is not None else url

        locations = []
        for place in posting.get("jobLocation") or []:
            location = _format_location(place)
            if location and location not in locations:
                locations.append(location)

        organization = posting.get("hiringOrganization") or {}
        company = organization.get("name") or "Juni Technology"

        if (
            not url
            or not title
            or not company
            or not description
            or not locations
            or not dedupe_key
            or dedupe_key in seen
        ):
            continue
        seen.add(dedupe_key)

        fields = {
            "url": url,
            "title": title,
            "company": company,
            "location": locations[0],
            "locations": locations,
            "description": description,
            "source": SITE,
        }

        if external_id is not None:
            fields["external_id"] = str(external_id)

        posted_at = posting.get("datePosted") or item.get("date_published")
        if posted_at:
            fields["posted_at"] = posted_at

        yield Job(**fields)
