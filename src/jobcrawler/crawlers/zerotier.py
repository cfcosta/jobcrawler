
import html
import json
import re
from datetime import datetime, timezone

SITE = "https://api.lever.co/v0/postings/zerotier?mode=json"
EXTRA_HOSTS = []


def _text(value):
    if not value:
        return None
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(?:p|div|li|ul|ol|h[1-6])\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _payload(response):
    if isinstance(response, str):
        return json.loads(response)
    if isinstance(response, (bytes, bytearray)):
        return json.loads(response.decode("utf-8"))
    if hasattr(response, "json"):
        return response.json()
    return json.loads(response.text)


def crawl(ctx):
    postings = _payload(ctx.fetch(SITE))
    seen = set()

    for posting in postings:
        posting_id = posting.get("id")
        url = posting.get("hostedUrl")
        identities = {value for value in (posting_id, url) if value}
        if not identities or seen.intersection(identities):
            continue
        seen.update(identities)

        sections = []
        intro = _text(posting.get("description"))
        if intro:
            sections.append(intro)

        for item in posting.get("lists") or []:
            heading = _text(item.get("text"))
            content = _text(item.get("content"))
            section = "\n".join(part for part in (heading, content) if part)
            if section:
                sections.append(section)

        additional = _text(posting.get("additional"))
        if additional:
            sections.append(additional)
        description = "\n\n".join(sections)

        categories = posting.get("categories") or {}
        created_at = posting.get("createdAt")
        posted_at = None
        if created_at is not None:
            posted_at = datetime.fromtimestamp(
                created_at / 1000, tz=timezone.utc
            ).isoformat()

        apply_url = posting.get("applyUrl")
        if not apply_url and url:
            apply_url = url.rstrip("/") + "/apply"

        fields = {
            "url": url,
            "title": posting.get("text"),
            "company": "ZeroTier",
            "location": categories.get("location"),
            "description": description,
            "source": SITE,
            "external_id": posting_id,
            "apply_url": apply_url,
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("team"),
            "posted_at": posted_at,
        }

        all_locations = categories.get("allLocations")
        if isinstance(all_locations, list):
            locations = [location for location in all_locations if location]
            if locations:
                fields["locations"] = locations

        yield {
            key: value for key, value in fields.items()
            if value is not None and value != ""
        }
